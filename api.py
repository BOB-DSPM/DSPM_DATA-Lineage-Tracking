from __future__ import annotations

from typing import Optional, List, Dict, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from modules.schema_sampler import sample_schema, parse_s3_uri
from modules.schema_store import save_schema, dataset_id_from_s3, get_version, list_versions
from modules.sql_lineage_light import extract_lineage
from modules.featurestore_schema import describe_feature_group, list_feature_groups

# ⚠️ 함수 직접 임포트 대신 모듈 별칭으로 임포트해 충돌/순환 import 방지
import lineage as lineage_lib

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(title="SageMaker Lineage API", version="1.4.0")

# CORS (운영에서는 특정 도메인만 허용 권장)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# boto3 공통 설정
# -----------------------------------------------------------------------------
_BOTO_CFG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=60,
)

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def _parse_regions(regions: Optional[str], profile: Optional[str]) -> List[str]:
    """
    regions 쿼리가 있으면 그것을 사용,
    없으면 boto3 Session에서 sagemaker 지원 모든 리전을 반환
    """
    if regions:
        parsed = [r.strip() for r in regions.split(",") if r.strip()]
    else:
        sess = boto3.session.Session(profile_name=profile) if profile else boto3.session.Session()
        parsed = sess.get_available_regions("sagemaker")
    return parsed


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

# -----------------------------------------------------------------------------
# 0) Health Check
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}

# -----------------------------------------------------------------------------
# 1) pipelines: 파이프라인 목록 + 태그/도메인 매핑
# -----------------------------------------------------------------------------
@app.get("/sagemaker/pipelines")
def sagemaker_pipelines(
    regions: Optional[str] = Query(None, description="쉼표구분 리전 목록. 없으면 SageMaker 지원 리전 전체"),
    includeLatestExec: bool = Query(False, description="파이프라인별 최신 실행 요약 포함"),
    profile: Optional[str] = Query(None, description="(개발/로컬) AWS 프로필명"),
    name: Optional[str] = Query(None, description="파이프라인 이름 부분일치 필터(선택)"),
    domainName: Optional[str] = Query(None, description="태그 DomainName=... 으로 필터(선택)"),
    domainId: Optional[str] = Query(None, description="태그 DomainId=... 으로 필터(선택)"),
):
    """
    리전별 '파이프라인 목록만' 반환. 각 파이프라인에는 태그 기반으로
    matchedDomain: {DomainId, DomainName} 가 매핑되어 함께 포함된다.
    """
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

# -----------------------------------------------------------------------------
# 2) 단건 라인리지 그래프
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# 3) 도메인에 매칭되는 모든 파이프라인 라인리지
# -----------------------------------------------------------------------------
@app.get("/lineage/by-domain")
def lineage_by_domain(
    region: str = Query(..., description="리전"),
    domain: str = Query(..., description="DomainName"),
    includeLatestExec: bool = Query(False),
    profile: str | None = Query(None),
    view: str = Query("both", regex="^(pipeline|data|both)$"),
):
    """
    해당 리전에서 DomainName 태그가 일치하는 파이프라인들을 전부 찾아
    각각의 라인리지를 수행해 한번에 반환
    """
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

# ... FastAPI app 생성 코드 뒤에 아래 라우트들 추가 ...

@app.post("/datasets/schema/scan")
def scan_dataset_schema(
    region: str = Query(..., description="e.g., ap-northeast-2"),
    s3_uri: str = Query(..., description="s3://bucket/prefix"),
    max_objects: int = Query(5, ge=1, le=50),
    max_bytes: int = Query(256*1024, ge=4096, le=5*1024*1024),
):
    """
    S3 prefix에서 소량 샘플을 읽어 JSON/CSV(+Parquet) 스키마를 추출하고 버전으로 저장.
    Confidence=Medium, Evidence=s3:getobject(sample)
    """
    sch = sample_schema(region=region, s3_uri=s3_uri, max_objects=max_objects, max_bytes=max_bytes)
    b, p = parse_s3_uri(s3_uri)
    dsid = dataset_id_from_s3(b, p)
    policy = {"region": region, "s3_uri": s3_uri, "max_objects": max_objects, "max_bytes": max_bytes}
    rec = save_schema(dsid, sch, policy)
    return {"ok": True, "dataset_id": dsid, "version": rec["version"], "schema": sch}

@app.get("/datasets/{bucket}/{prefix:path}/schema")
def get_dataset_schema(bucket: str, prefix: str, version: int | None = None):
    """
    저장된 스키마 버전 조회(미지정 시 최신)
    """
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

@app.post("/sql/lineage")
def post_sql_lineage(sql: str):
    """
    간이 SQL → 컬럼 라인리지(라이트 파서).
    정확도 향상하려면 sqlglot 기반 파서로 교체 권장.
    """
    out = extract_lineage(sql)
    if not out:
        return {"ok": False, "note": "unsupported or parse failed"}
    return {"ok": True, **out}

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

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # 개발 기본 포트 8300 (로그 일관)
    uvicorn.run(app, host="0.0.0.0", port=8300)