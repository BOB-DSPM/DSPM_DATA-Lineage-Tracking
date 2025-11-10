from __future__ import annotations

from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
import os, re, json, shutil, asyncio

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import httpx

# --- Internal modules ---
from modules.schema_sampler import sample_schema, parse_s3_uri
from modules.schema_store import save_schema, dataset_id_from_s3, get_version, list_versions
from modules.featurestore_schema import describe_feature_group, list_feature_groups
from modules.sql_collector import collect_from_repo
from modules.sql_lineage_store import put, get_by_pipeline, get_by_job
from modules.connectors.git_fetch import shallow_clone
from modules.sql_try import try_parse

# 순환 import 방지용 별칭 임포트 (lineage.py)
import lineage as lineage_lib

# -----------------------------------------------------------------------------#
# FastAPI
# -----------------------------------------------------------------------------#
app = FastAPI(title="SageMaker Lineage API", version="1.6.1")

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
S3_RE = re.compile(r"^s3://([^/]+)/?(.*)$")

def data_node_id_from_uri(uri: str) -> str:
    # 프론트 데이터 노드 id 규칙에 맞춰 통일
    return f"data:{uri}"

def is_data_uri(uri: str) -> bool:
    """
    코드/모델 파일 등 데이터가 아닌 대상은 제외
    """
    if not isinstance(uri, str):
        return False
    if not uri.startswith("s3://"):
        return False
    lower = uri.lower()
    if lower.endswith(".py") or lower.endswith(".ipynb") or lower.endswith(".tar.gz") or lower.endswith(".model"):
        return False
    return True

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

async def fetch_schema_layer(
    request: Request,
    pipeline: str,
    region: str,
    include_featurestore: bool = True,
    include_sql: bool = True,
    scan_if_missing: bool = False,   # 스키마 없으면 자동 스캔
    timeout_s: float = 15.0,
) -> Dict[str, Any]:
    """
    /lineage/schema 집계 로직:
      - /lineage → 데이터 관점 그래프에서 dataArtifact 노드 추출
      - 각 S3 URI에 대해 /datasets/{bucket}/{prefix}/schema 호출
      - (옵션) FeatureStore, SQL 라인리지 보강
      - 프론트가 바로 쓰는 tables/columns/featureGroups/features + links 반환
    """
    base = str(request.base_url).rstrip("/")
    warnings: List[str] = []

    async with httpx.AsyncClient(base_url=base, timeout=timeout_s) as client:
        # 1) 라인리지(graphData 확보)
        r = await client.get("/lineage", params={"pipeline": pipeline, "region": region, "view": "data"})
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"lineage fetch failed: {r.text}")
        lineage = r.json() or {}
        data_graph = lineage.get("graphData") or {}
        nodes = data_graph.get("nodes") or []

        # 2) dataArtifact -> S3 URI 매핑
        artifact_map: Dict[str, str] = {}  # nodeId -> s3://...
        for dn in (n for n in nodes if n.get("type") == "dataArtifact"):
            node_id = dn.get("id") or ""
            # id 규칙이 data:s3://... 인 케이스
            if node_id.startswith("data:s3://"):
                s3 = node_id[5:]
                if is_data_uri(s3):
                    artifact_map[node_id] = s3
                continue
            # uri 필드에서 추출
            uri = dn.get("uri")
            if is_data_uri(uri):
                mapped_id = data_node_id_from_uri(uri)
                artifact_map[mapped_id] = uri

        # 후보 URI 정리
        uris = sorted(set(artifact_map.values()))

        # URI가 없어도 계속 진행 (SQL 라인리지에서 테이블 정보 가져올 수 있음)
        if not uris:
            warnings.append("no data artifacts found in lineage graph")

        # (선택) 스캔 트리거
        if scan_if_missing and uris:
            for u in uris:
                try:
                    await client.post(
                        "/datasets/schema/scan",
                        params={"region": region, "s3_uri": u},
                    )
                except Exception as e:
                    warnings.append(f"schema scan failed for {u}: {e}")

        # 3) 스키마 fetch with fallback(latest version)
        async def fetch_dataset_schema(uri: str) -> Dict[str, Any]:
            try:
                bucket, prefix = parse_s3_uri(uri)
                parts = [p for p in prefix.split("/") if p]
                tried = []
                # uri에서 상위 폴더로 한 단계씩 올라가며 /schema 조회
                for i in range(len(parts), 0, -1):
                    candidate = "/".join(parts[:i])
                    tried.append(candidate)
                    res = await client.get(f"/datasets/{bucket}/{candidate}/schema")
                    if res.status_code == 200:
                        data = res.json() or {}
                        # 어떤 prefix에 매칭됐는지 같이 반환
                        return {
                            "uri": uri,
                            "ok": True,
                            "data": data,
                            "bucket": bucket,
                            "matched_prefix": candidate,
                        }
                # 마지막으로 제일 상위 prefix도 안 되면 실패
                return {"uri": uri, "ok": False, "error": f"no schema for {tried}"}
            except Exception as e:
                return {"uri": uri, "ok": False, "error": str(e)}

        dataset_results = []
        if uris:
            dataset_results = await asyncio.gather(*(fetch_dataset_schema(u) for u in uris))

        # 4) normalize -> tables/columns
        tables: List[Dict[str, Any]] = []
        columns: List[Dict[str, Any]] = []

        for res in dataset_results:
            if not res["ok"]:
                warnings.append(f"{res['uri']}: {res.get('error')}")
                continue

            data = res["data"] or {}
            bucket = res["bucket"]
            matched_prefix = res["matched_prefix"]

            # dataset_id 없으면 matched_prefix로 구성
            dataset_id = (
                data.get("dataset_id")
                or data.get("id")
                or f"s3://{bucket}/{matched_prefix}"
            )

            # table 이름: dataset_id 마지막 토큰 (pipelines::exp1 -> exp1)
            raw_name = dataset_id.split("::")[-1]
            t_name = raw_name.rstrip("/").split("/")[-1]
            t_id = f"table:{t_name}"

            # 이 스키마가 커버하는 data 노드들과 연결
            links: List[str] = []
            prefix_uri = f"s3://{bucket}/{matched_prefix}"
            for node_id, s3 in artifact_map.items():
                if s3.startswith(prefix_uri):
                    links.append(node_id)
            links = sorted(set(links))

            # links가 없어도 테이블은 추가 (프론트에 표시하기 위해)
            tables.append({
                "id": t_id,
                "name": t_name,
                "version": data.get("version"),
                "links": links,
                "s3_prefix": matched_prefix,  # 디버깅용
            })

            # ----- 컬럼 추출 -----
            # 1) columns 배열 형식이 있으면 우선
            raw_cols = data.get("columns")

            # 2) schema.fields 형식 파싱
            if not raw_cols and isinstance(data.get("schema"), dict):
                fields = data["schema"].get("fields") or {}
                raw_cols = []
                for cname, meta in fields.items():
                    if cname in ("sampled_files",):
                        continue
                    ctype = None
                    if isinstance(meta, dict):
                        ts = meta.get("types") or meta.get("type")
                        if isinstance(ts, list):
                            ctype = " | ".join(str(t) for t in ts)
                        else:
                            ctype = ts
                    raw_cols.append({"name": cname, "type": ctype})

            for c in raw_cols or []:
                cname = c.get("name")
                if not cname:
                    continue
                ctype = c.get("type")
                columns.append({
                    "id": f"column:{t_name}.{cname}",
                    "name": cname,
                    "tableId": t_id,
                    "type": ctype,
                    "links": links,
                })

        # 5) SQL 라인리지로 테이블 보강 (스키마가 없을 때 중요!)
        if include_sql:
            try:
                sqlr = await client.get(f"/pipelines/{pipeline}/sql-lineage", params={"region": region})
                if sqlr.status_code == 200:
                    sql = sqlr.json() or {}
                    sql_tables = sql.get("steps", [])
                    # SQL에서 발견된 테이블 추가
                    for step in sql_tables:
                        dst = step.get("dst")
                        if not dst:
                            continue
                        t_name = dst.split(".")[-1]  # schema.table -> table
                        t_id = f"table:{t_name}"
                        if any(t["id"] == t_id for t in tables):
                            continue
                        tables.append({
                            "id": t_id,
                            "name": t_name,
                            "version": None,
                            "links": [],
                            "source": "sql",
                            "step": step.get("step"),
                        })
                        for col_name in (step.get("columns") or []):
                            if not col_name:
                                continue
                            columns.append({
                                "id": f"column:{t_name}.{col_name}",
                                "name": col_name,
                                "tableId": t_id,
                                "type": "unknown",
                                "links": [],
                                "source": "sql",
                            })
                    # 기존 테이블 정보 보강
                    sql_tables_map = { t.get("dst"): t for t in sql_tables if t.get("dst") }
                    for t in tables:
                        if t.get("source") == "sql":
                            continue
                        st = sql_tables_map.get(t["name"])
                        if st:
                            t["sql_step"] = st.get("step")
                            t["sql_file"] = st.get("file")
            except Exception as e:
                warnings.append(f"sql lineage: {e}")

        # 6) Feature Store
        feature_groups: List[Dict[str, Any]] = []
        features: List[Dict[str, Any]] = []
        if include_featurestore:
            try:
                fg_list = await client.get("/featurestore/feature-groups", params={"region": region})
                if fg_list.status_code == 200:
                    for fg in (fg_list.json().get("items", []) or []):
                        name = fg.get("FeatureGroupName") or fg.get("name")
                        if not name:
                            continue
                        det = await client.get(f"/featurestore/feature-groups/{name}", params={"region": region})
                        if det.status_code != 200:
                            continue
                        detj = det.json() or {}
                        s3_uri = (detj.get("OfflineStoreConfig") or {}).get("S3StorageConfig", {}).get("ResolvedOutputS3Uri")
                        links = [data_node_id_from_uri(s3_uri)] if s3_uri and is_data_uri(s3_uri) else []
                        fg_id = f"featureGroup:{name}"
                        feature_groups.append({
                            "id": fg_id,
                            "name": name,
                            "version": detj.get("Version") or detj.get("FeatureGroupVersion"),
                            "links": links,
                        })
                        for f in (detj.get("FeatureDefinitions") or detj.get("Features") or []):
                            fname = f.get("FeatureName") or f.get("name")
                            ftype = f.get("FeatureType") or f.get("type")
                            if not fname:
                                continue
                            features.append({
                                "id": f"feature:{name}.{fname}",
                                "name": fname,
                                "groupId": fg_id,
                                "type": ftype,
                                "links": links,
                            })
            except Exception as e:
                warnings.append(f"feature store: {e}")

        # id 기준으로 중복 제거
        tables = list({t["id"]: t for t in tables}.values())
        columns = list({c["id"]: c for c in columns}.values())

        return {
            "tables": tables,
            "columns": columns,
            "featureGroups": feature_groups,
            "features": features,
            "warnings": warnings,
        }

# -----------------------------------------------------------------------------#
# 0) Health
# -----------------------------------------------------------------------------#
@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}

# -----------------------------------------------------------------------------#
# NEW) 스키마 집계 API (프론트의 /schema 호출 대응)
#      - /schema 와 /lineage/schema 두 경로 모두 제공
# -----------------------------------------------------------------------------#
@app.get("/schema")
async def api_schema(
    request: Request,
    pipeline: str = Query(...),
    region: str = Query(...),
    include_featurestore: bool = Query(True),
    include_sql: bool = Query(True),
    scan_if_missing: bool = Query(False),
    timeout_s: float = Query(15.0, ge=3.0, le=120.0),
):
    return await fetch_schema_layer(
        request=request,
        pipeline=pipeline,
        region=region,
        include_featurestore=include_featurestore,
        include_sql=include_sql,
        scan_if_missing=scan_if_missing,
        timeout_s=timeout_s,
    )

@app.get("/lineage/schema")
async def api_lineage_schema(
    request: Request,
    pipeline: str = Query(...),
    region: str = Query(...),
    include_featurestore: bool = Query(True),
    include_sql: bool = Query(True),
    scan_if_missing: bool = Query(False),
    timeout_s: float = Query(15.0, ge=3.0, le=120.0),
):
    return await api_schema(
        request=request,
        pipeline=pipeline,
        region=region,
        include_featurestore=include_featurestore,
        include_sql=include_sql,
        scan_if_missing=scan_if_missing,
        timeout_s=timeout_s,
    )

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
    ### NEW
    includePII: bool = Query(False, description="Analyzer와 Retention(삭제된 ID 교차점검) 포함"),
):
    try:
        data = lineage_lib.get_lineage_json(
            region=region,
            pipeline_name=pipeline,
            domain_name=domain,
            include_latest_exec=includeLatestExec,
            profile=profile,
            view=view,
            include_pii=includePII,
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
    includePII: bool = Query(False, description="Analyzer+Retention 포함"),
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
                    include_pii=includePII,
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
# 9) v2 Scan Endpoints (RDS Auto / Cross-Check 저장 및 리포트)
#    ⬇ 기본 저장 경로를 ./result 로 변경
# -----------------------------------------------------------------------------#
RESULT_DIR = os.getenv("SCAN_RESULT_DIR", "./result")
os.makedirs(RESULT_DIR, exist_ok=True)

RDS_AUTO_PATH = os.getenv("RDS_AUTO_REPORT", os.path.join(RESULT_DIR, "rds_auto_scan_report.json"))
XCHECK_PATH   = os.getenv("XCHECK_REPORT",   os.path.join(RESULT_DIR, "cross_check_report.json"))

class RdsAutoReq(BaseModel):
    collector_api: str = Field(..., description="예: http://43.202.228.52:8000")
    default_user: str | None = "madeit"
    default_password: str | None = "madeit1022!"
    passwords: dict[str, str] | None = None

class XcheckReq(BaseModel):
    collector_api: str = Field(..., description="예: http://43.202.228.52:8000")
    bucket_names: list[str] | None = None
    file_extensions: list[str] | None = [".csv", ".json", ".jsonl"]
    max_files_per_bucket: int | None = 100

def _safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _index_deleted_ids_from_rds_report(js: dict) -> set[str]:
    # 동일 로직을 간단 복제 (lineage_lib에서 import해도 무방)
    ids: set[str] = set()
    if not isinstance(js, dict):
        return ids
    items = js.get("items") or js.get("data") or []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            idv = it.get("id") or it.get("user_id") or it.get("identifier") or it.get("record_id")
            st  = (it.get("status") or it.get("state") or "").lower()
            if idv and st in {"deleted", "expired", "removed"}:
                ids.add(str(idv))
    for k in ("deleted_ids", "expired_ids", "removed_ids"):
        arr = js.get(k)
        if isinstance(arr, list):
            for v in arr:
                if v is not None:
                    ids.add(str(v))
    return ids

def _index_s3_usage_from_xcheck(js: dict) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    if not isinstance(js, (dict, list)):
        return index

    def _add(uri: str, ids: list):
        if not uri:
            return
        uri = uri.strip()
        if not uri.startswith("s3://"):
            if uri.startswith("s3/"):
                parts = uri[3:].split("/", 1)
                if parts and len(parts) == 2:
                    uri = f"s3://{parts[0]}/{parts[1]}"
        if not uri.startswith("s3://"):
            return
        index.setdefault(uri, set()).update({str(x) for x in ids if x is not None})

    if isinstance(js, dict):
        items = js.get("items") or js.get("data") or []
        if isinstance(items, list) and items:
            for it in items:
                if not isinstance(it, dict):
                    continue
                uri = it.get("file") or it.get("source") or it.get("s3_uri")
                ids = it.get("matched_ids") or it.get("ids") or it.get("identifiers") or []
                if isinstance(ids, dict) and "items" in ids:
                    ids = ids["items"]
                if isinstance(ids, list):
                    _add(uri, ids)
        else:
            for k, v in js.items():
                if isinstance(v, dict):
                    ids = v.get("ids") or v.get("matched_ids") or []
                    if isinstance(ids, list):
                        _add(k, ids)

    if isinstance(js, list):
        for it in js:
            if isinstance(it, dict):
                uri = it.get("file") or it.get("source") or it.get("s3_uri")
                ids = it.get("matched_ids") or it.get("ids") or []
                if isinstance(ids, list):
                    _add(uri, ids)

    return index

@app.post("/api/v2/scan/rds-auto")
async def api_v2_scan_rds_auto(req: RdsAutoReq):
    """
    RDS에서 '삭제된(=보존만료)' ID 목록을 수집하여 로컬 리포트 저장
    """
    url = f"{req.collector_api.rstrip('/')}/api/v2/scan/rds-auto"
    payload = req.model_dump()
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"collector error: {r.text}")
        data = r.json()
    try:
        with open(RDS_AUTO_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"save failed: {e}")
    return {"ok": True, "saved": RDS_AUTO_PATH}

@app.post("/api/v2/scan/cross-check")
async def api_v2_scan_cross_check(req: XcheckReq):
    """
    S3에서 파일을 훑어 '삭제된 ID' 사용 여부 교차점검 → 로컬 리포트 저장
    """
    url = f"{req.collector_api.rstrip('/')}/api/v2/scan/cross-check"
    payload = req.model_dump()
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(url, json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"collector error: {r.text}")
        data = r.json()
    try:
        with open(XCHECK_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"save failed: {e}")
    return {"ok": True, "saved": XCHECK_PATH}

@app.get("/api/v2/scan/cross-check/report")
def api_v2_scan_cross_check_report():
    """
    저장된 RDS/교차점검 리포트를 함께 요약 반환
    """
    rds = _safe_load_json(RDS_AUTO_PATH) or {}
    xc  = _safe_load_json(XCHECK_PATH) or {}

    deleted_ids = _index_deleted_ids_from_rds_report(rds)
    usage = _index_s3_usage_from_xcheck(xc)

    affected = []
    for uri, ids in usage.items():
        inter = sorted(list(ids.intersection(deleted_ids)))
        if inter:
            affected.append({"s3": uri, "count": len(inter), "sample": inter[:10]})

    summary = {
        "deletedIdsTotal": len(deleted_ids),
        "s3ObjectsChecked": len(usage),
        "affectedObjects": len(affected),
    }
    return {"summary": summary, "affected": affected}

# -----------------------------------------------------------------------------#
# Entrypoint
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8300)
