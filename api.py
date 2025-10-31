from __future__ import annotations
from typing import Optional, List, Dict, Any
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from pydantic import BaseModel
import os
import boto3
import uvicorn
import shutil

# --- Internal modules ---
from modules.schema_sampler import sample_schema, parse_s3_uri
from modules.schema_store import save_schema, dataset_id_from_s3, get_version, list_versions
from modules.featurestore_schema import describe_feature_group, list_feature_groups
from modules.sql_collector import collect_from_repo
from modules.sql_lineage_store import put, get_by_pipeline, get_by_job
from modules.connectors.git_fetch import shallow_clone
from modules.sql_try import try_parse

# 순환 import 방지용 별칭 임포트
import lineage as lineage_lib

# -----------------------------------------------------------------------------#
# FastAPI
# -----------------------------------------------------------------------------#
app = FastAPI(title="SageMaker Lineage API", version="1.5.1")

# CORS (운영 시 특정 도메인으로 제한 권장)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],      # ← POST/OPTIONS 모두 허용
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------#
# boto3 공통 설정
# -----------------------------------------------------------------------------#
_BOTO_CFG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=60,
)

# -----------------------------------------------------------------------------#
# Utils
# -----------------------------------------------------------------------------#
def _parse_regions(regions: Optional[str], profile: Optional[str]) -> List[str]:
    """regions 쿼리가 있으면 그것을 사용, 없으면 SageMaker 지원 모든 리전 반환"""
    if regions:
        return [r.strip() for r in regions.split(",") if r.strip()]
    sess = boto3.session.Session(profile_name=profile) if profile else boto3.session.Session()
    return sess.get_available_regions("sagemaker")

def _get_latest_pipeline_execution(sm, pipeline_name: str) -> Dict[str, Any]:
    """최신 파이프라인 실행 1건 요약"""
    resp = sm.list_pipeline_executions(
        PipelineName=pipeline_name,
        SortBy="CreationTime",
        SortOrder="Descending",
        MaxResults=1
    )
    exes = resp.get("PipelineExecutionSummaries", [])
    if not exes:
        return {}
    x = exes[0]
    return {
        "arn": x.get("PipelineExecutionArn"),
        "status": x.get("PipelineExecutionStatus"),
        "startTime": x.get("StartTime").isoformat() if x.get("StartTime") else None,
        "lastModifiedTime": x.get("LastUpdatedTime").isoformat() if x.get("LastUpdatedTime") else None
    }

def guess_step_from_path(path: str, pipeline: str) -> str:
    import re, os as _os
    base = _os.path.basename(path or "")
    m = re.match(r"(\d+_)?([a-zA-Z0-9\-_]+)", base)
    step = (m.group(2) if m else base).replace(".sql", "").replace(".py", "")
    return step

# -----------------------------------------------------------------------------#
# 0) Health
# -----------------------------------------------------------------------------#
@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}

# -----------------------------------------------------------------------------#
# 1) pipelines: 파이프라인 목록 + 태그/도메인 매핑
# -----------------------------------------------------------------------------#
@app.get("/sagemaker/pipelines")
def sagemaker_pipelines(
    regions: Optional[str] = Query(None, description="쉼표구분 리전 목록. 없으면 SageMaker 지원 리전 전체"),
    includeLatestExec: bool = Query(False, description="파이프라인별 최신 실행 요약 포함"),
    profile: Optional[str] = Query(None, description="(개발/로컬) AWS 프로필명"),
    name: Optional[str] = Query(None, description="파이프라인 이름 부분일치 필터(선택)"),
    domainName: Optional[str] = Query(None, description="태그 DomainName=... 으로 필터(선택)"),
    domainId: Optional[str] = Query(None, description="태그 DomainId=... 으로 필터(선택)"),
):
    try:
        region_list = _parse_regions(regions, profile)
        out: List[Dict[str, Any]] = []

        for r in region_list:
            try:
                pipes = lineage_lib.list_pipelines_with_domain(region=r, profile=profile)

                if name:
                    s = name.lower()
                    pipes = [p for p in pipes if s in p["name"].lower()]
                if domainName:
                    dn = domainName.lower()
                    pipes = [p for p in pipes
                             if (p.get("matchedDomain") and p["matchedDomain"].get("DomainName","").lower() == dn)
                             or ((p.get("tags") or {}).get("DomainName","").lower() == dn)]
                if domainId:
                    pipes = [
                        p for p in pipes
                        if (p.get("matchedDomain") and p["matchedDomain"].get("DomainId") == domainId)
                        or ((p.get("tags") or {}).get("DomainId") == domainId)
                    ]

                if includeLatestExec:
                    sess = boto3.session.Session(profile_name=profile, region_name=r) if profile \
                        else boto3.session.Session(region_name=r)
                    sm = sess.client("sagemaker", config=_BOTO_CFG)
                    for p in pipes:
                        try:
                            p["latestExecution"] = _get_latest_pipeline_execution(sm, p["name"])
                        except Exception:
                            p["latestExecution"] = {}

                out.append({"region": r, "pipelines": pipes})
            except Exception as e:
                out.append({"region": r, "error": str(e), "pipelines": []})

        return {"regions": out}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sagemaker pipelines error: {e}")

# -----------------------------------------------------------------------------#
# 2) 단건 라인리지
# -----------------------------------------------------------------------------#
@app.get("/lineage")
def lineage_endpoint(
    pipeline: str = Query(..., description="SageMaker Pipeline Name"),
    region: str = Query(..., description="e.g., ap-northeast-2"),
    domain: str | None = Query(None, description="Optional SageMaker DomainName tag filter"),
    includeLatestExec: bool = Query(False, description="Include latest execution info"),
    profile: str | None = Query(None, description="Local dev only; AWS profile name"),
    view: str = Query("both", regex="^(pipeline|data|both)$", description="pipeline | data | both"),
):
    try:
        data = lineage_lib.get_lineage_json(
            region=region,
            pipeline_name=pipeline,
            domain_name=domain,
            include_latest_exec=includeLatestExec,
            profile=profile,
            view=view,
        )
        return data
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except ClientError as ce:
        err = ce.response.get("Error", {})
        raise HTTPException(
            status_code=502,
            detail={"type":"AWSClientError","code":err.get("Code"),"message":err.get("Message")}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"type":"ServerError","message":str(e)})

# -----------------------------------------------------------------------------#
# 3) 도메인 내 모든 파이프라인 라인리지
# -----------------------------------------------------------------------------#
@app.get("/lineage/by-domain")
def lineage_by_domain(
    region: str = Query(..., description="리전"),
    domain: str = Query(..., description="DomainName"),
    includeLatestExec: bool = Query(False),
    profile: str | None = Query(None),
    view: str = Query("both", regex="^(pipeline|data|both)$"),
):
    try:
        pipes = lineage_lib.list_pipelines_with_domain(region=region, profile=profile)
        targets = [
            p["name"] for p in pipes
            if (p.get("matchedDomain") and p["matchedDomain"].get("DomainName") == domain)
            or ((p.get("tags") or {}).get("DomainName") == domain)
        ]
        if not targets:
            raise HTTPException(status_code=404, detail=f"no pipelines tagged with DomainName={domain} in {region}")

        results = []
        for name in targets:
            try:
                data = lineage_lib.get_lineage_json(
                    region=region,
                    pipeline_name=name,
                    domain_name=domain,
                    include_latest_exec=includeLatestExec,
                    profile=profile,
                    view=view,
                )
                results.append({"pipeline": name, "ok": True, "data": data})
            except Exception as e:
                results.append({"pipeline": name, "ok": False, "error": str(e)})

        return {"region": region, "domain": domain, "count": len(results), "results": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"by-domain error: {e}")

# -----------------------------------------------------------------------------#
# 4) Dataset schema endpoints
# -----------------------------------------------------------------------------#
@app.post("/datasets/schema/scan")
def scan_dataset_schema(
    region: str = Query(..., description="e.g., ap-northeast-2"),
    s3_uri: str = Query(..., description="s3://bucket/prefix"),
    max_objects: int = Query(5, ge=1, le=50),
    max_bytes: int = Query(256*1024, ge=4096, le=5*1024*1024),
):
    """S3 prefix에서 샘플을 읽어 JSON/CSV(+Parquet) 스키마 추출 후 버전으로 저장"""
    sch = sample_schema(region=region, s3_uri=s3_uri, max_objects=max_objects, max_bytes=max_bytes)
    b, p = parse_s3_uri(s3_uri)
    dsid = dataset_id_from_s3(b, p)
    policy = {"region": region, "s3_uri": s3_uri, "max_objects": max_objects, "max_bytes": max_bytes}
    rec = save_schema(dsid, sch, policy)
    return {"ok": True, "dataset_id": dsid, "version": rec["version"], "schema": sch}

@app.get("/datasets/{bucket}/{prefix:path}/schema")
def get_dataset_schema(bucket: str, prefix: str, version: int | None = None):
    """저장된 스키마 버전 조회(미지정 시 최신)"""
    dsid = dataset_id_from_s3(bucket, prefix)
    rec = get_version(dsid, version)
    if not rec:
        raise HTTPException(404, f"schema not found: {dsid}")
    return {"dataset_id": dsid, "version": rec["version"], "policy": rec["policy"], "schema": rec["schema"]}

@app.get("/datasets/{bucket}/{prefix:path}/schema/versions")
def list_dataset_schema_versions(bucket: str, prefix: str):
    dsid = dataset_id_from_s3(bucket, prefix)
    vers = list_versions(dsid)
    return [{"version": v["version"], "sampled_at": v["sampled_at"], "policy": v["policy"]} for v in vers]

# -----------------------------------------------------------------------------#
# 5) SQL 단건 파싱 테스트
# -----------------------------------------------------------------------------#
@app.post("/sql/lineage")
def api_sql_lineage(sql: str = Query(...), dialect: str | None = Query(None)):
    return try_parse(sql, dialect=dialect)

# -----------------------------------------------------------------------------#
# 6) Feature Store helpers
# -----------------------------------------------------------------------------#
@app.get("/featurestore/feature-groups")
def api_list_feature_groups(
    region: str,
    profile: str | None = None,
    nameContains: str | None = None,
):
    return {"items": list_feature_groups(region=region, profile=profile, name_contains=nameContains)}

@app.get("/featurestore/feature-groups/{name}")
def api_describe_feature_group(
    name: str,
    region: str,
    profile: str | None = None,
):
    return describe_feature_group(region=region, name=name, profile=profile)

# -----------------------------------------------------------------------------#
# 7) SQL 자동 수집 (repo_path or git_url)
# -----------------------------------------------------------------------------#
USE_SQL_AUTOCOLLECT = os.getenv("USE_SQL_AUTOCOLLECT", "true").lower() == "true"

if USE_SQL_AUTOCOLLECT:

    @app.post("/tasks/sql/refresh")
    def refresh_sql_lineage(
        # A) 기존 로컬 경로 방식
        repo_path: str | None = Query(None, description="레포 루트 경로"),

        # B) 원격 Git 방식
        git_url: str | None = Query(None, description="Git HTTPS URL"),
        branch: str = Query("main", description="Git branch"),
        subdir: str | None = Query(None, description="Git sub-directory (예: models)"),
        token: str | None = Query(None, description="Git token/PAT (필요 시)"),

        # 공통
        pipeline: str = Query(...),
        job_id: str | None = Query(None),
        dialect: str | None = Query(None),
    ):
        """
        repo_path 또는 git_url 중 '하나'는 필수.
        수집된 SQL은 try_parse → put() 으로 저장합니다.
        """
        if (repo_path is None) and (git_url is None):
            raise HTTPException(status_code=400, detail="repo_path or git_url required")

        tmp_dir: Path | None = None
        try:
            # 1) 소스 결정
            if git_url:
                tmp_dir = shallow_clone(git_url=git_url, branch=branch, subdir=subdir, token=token)
                scan_root = str(tmp_dir)  # collect_from_repo는 str 경로 기대
            else:
                scan_root_path = Path(repo_path).expanduser().resolve()
                if not scan_root_path.exists():
                    raise HTTPException(status_code=404, detail=f"repo_path not found: {scan_root_path}")
                scan_root = str(scan_root_path)

            # 2) 수집 → 파싱 → 저장
            items = collect_from_repo(scan_root)
            saved = 0
            for it in items:
                parsed = try_parse(it.get("sql", ""), dialect=dialect)
                if not parsed.get("ok"):
                    continue
                rec = {
                    "pipeline": pipeline,
                    "step": guess_step_from_path(it.get("file", ""), pipeline),
                    "job_id": job_id,
                    "file": it.get("file"),
                    "sql": it.get("sql"),
                    "parsed": parsed,
                }
                put(rec)
                saved += 1

            return {"ok": True, "saved": saved, "pipeline": pipeline}

        finally:
            # 3) 임시 깃 클론 디렉터리 정리
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    @app.get("/jobs/{job_id}/sql-lineage")
    def sql_lineage_by_job(job_id: str):
        return {"ok": True, "job_id": job_id, "items": get_by_job(job_id)}

    @app.get("/pipelines/{name}/sql-lineage")
    def sql_lineage_by_pipeline(name: str):
        rows = get_by_pipeline(name)
        latest: dict[str, dict] = {}
        for r in rows:
            step = r.get("step")
            cur = latest.get(step)
            if (not cur) or r.get("ts", 0) > cur.get("ts", 0):
                latest[step] = r
        summary = []
        for step, r in latest.items():
            p = r.get("parsed", {})
            summary.append({
                "step": step,
                "dst": p.get("dst"),
                "sources": p.get("sources", []),
                "columns": p.get("columns", []),
                "file": r.get("file"),
                "ts": r.get("ts"),
            })
        return {"ok": True, "pipeline": name, "steps": summary}

# -----------------------------------------------------------------------------#
# 8) 레포 없이 바로 체험: Inline SQL 파싱 저장
# -----------------------------------------------------------------------------#
class InlineSqlReq(BaseModel):
    pipeline: str
    sql: str | None = None
    sql_list: List[str] | None = None
    job_id: str | None = None
    dialect: str | None = None

@app.post("/tasks/sql/inline", summary="Parse & Store Inline SQL (no repo needed)")
def task_sql_inline(req: InlineSqlReq):
    payload: List[str] = []
    if req.sql:
        payload.append(req.sql)
    if req.sql_list:
        payload.extend(req.sql_list)
    if not payload:
        raise HTTPException(status_code=400, detail="sql or sql_list required")

    saved = 0
    for i, sql in enumerate(payload, start=1):
        parsed = try_parse(sql, dialect=req.dialect)
        if not parsed.get("ok"):
            continue
        put({
            "pipeline": req.pipeline,
            "job_id": req.job_id,
            "step": f"inline::{i}",
            "file": f"inline::{i}",
            "sql": sql,
            "parsed": parsed
        })
        saved += 1
    return {"ok": True, "saved": saved, "pipeline": req.pipeline}

# -----------------------------------------------------------------------------#
# Entrypoint
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8300)
