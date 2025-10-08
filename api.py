# api.py
from __future__ import annotations

import os
import time
from typing import Optional, List, Dict, Any

import boto3
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from lineage import (
    get_lineage_json,
    build_inventory,          # 리전→도메인→파이프라인 카탈로그 생성
)

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(title="SageMaker Lineage API", version="1.3.0")

# CORS (운영에서는 특정 도메인만 허용 권장)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# 공통 설정
# -----------------------------------------------------------------------------
_BOTO_CFG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=60
)

# 운영에서 리전 스캔 범위를 제한하고 싶으면 환경변수로 지정
# 예) ALLOWED_REGIONS="ap-northeast-2,us-east-1"
_ALLOWED_REGIONS_ENV = os.getenv("ALLOWED_REGIONS")
_ALLOWED_REGIONS = (
    {r.strip() for r in _ALLOWED_REGIONS_ENV.split(",")} if _ALLOWED_REGIONS_ENV else None
)

# 간단 메모리 캐시(선택): overview 결과 캐싱 TTL(초) — 트래픽 많은 환경에서 UX 개선
_OVERVIEW_CACHE: Dict[str, Any] = {"ts": 0, "key": "", "data": None}
_OVERVIEW_TTL_SECONDS = int(os.getenv("OVERVIEW_TTL_SECONDS", "0"))  # 0이면 캐싱 비활성화


# -----------------------------------------------------------------------------
# Utils for /sagemaker/overview
# -----------------------------------------------------------------------------
def _parse_regions(regions: Optional[str], profile: Optional[str]) -> List[str]:
    """
    regions 쿼리가 있으면 그것을 사용,
    없으면 boto3 Session에서 sagemaker 지원 모든 리전을 반환
    (운영에서는 ALLOWED_REGIONS 교집합으로 제한 가능)
    """
    if regions:
        parsed = [r.strip() for r in regions.split(",") if r.strip()]
    else:
        sess = boto3.session.Session(profile_name=profile) if profile else boto3.session.Session()
        parsed = sess.get_available_regions("sagemaker")

    if _ALLOWED_REGIONS:
        parsed = [r for r in parsed if r in _ALLOWED_REGIONS]

    return parsed


def _list_all_domains(sm) -> List[Dict[str, Any]]:
    """
    SageMaker ListDomains (Paginator)
    """
    paginator = sm.get_paginator("list_domains")
    items: List[Dict[str, Any]] = []
    for page in paginator.paginate():
        for d in page.get("Domains", []):
            items.append({
                "domainId": d.get("DomainId"),
                "domainArn": d.get("DomainArn"),
                "domainName": d.get("DomainName"),
                "status": d.get("Status"),
                "url": d.get("Url"),
                "homeEfsFileSystemId": d.get("HomeEfsFileSystemId"),
                "creationTime": d.get("CreationTime").isoformat() if d.get("CreationTime") else None
            })
    return items


def _get_latest_pipeline_execution(sm, pipeline_name: str) -> Dict[str, Any]:
    """
    최신 파이프라인 실행 1건 요약
    """
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


def _list_all_pipelines(sm, include_latest: bool) -> List[Dict[str, Any]]:
    """
    SageMaker ListPipelines (Paginator)
    include_latest=True면 최신 실행 1건 붙임
    """
    paginator = sm.get_paginator("list_pipelines")
    items: List[Dict[str, Any]] = []
    for page in paginator.paginate(SortBy="CreationTime", SortOrder="Descending"):
        for p in page.get("PipelineSummaries", []):
            row = {
                "pipelineName": p.get("PipelineName"),
                "pipelineArn": p.get("PipelineArn"),
                "created": p.get("CreationTime").isoformat() if p.get("CreationTime") else None,
                "lastModifiedTime": p.get("LastModifiedTime").isoformat() if p.get("LastModifiedTime") else None
            }
            if include_latest and p.get("PipelineName"):
                try:
                    row["latestExecution"] = _get_latest_pipeline_execution(sm, p["PipelineName"])
                except Exception:
                    # 최신 실행 조회 실패는 치명적이지 않으므로 무시하고 진행
                    row["latestExecution"] = {}
            items.append(row)
    return items


# -----------------------------------------------------------------------------
# 0) Health Check
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


# -----------------------------------------------------------------------------
# 1) Catalog : Region→Domain→Pipeline (/sagemaker/catalog)
# -----------------------------------------------------------------------------
@app.get("/sagemaker/catalog")
def sagemaker_catalog(
    regions: str | None = Query(None, description="쉼표구분 리전 목록. 미지정 시 SageMaker 지원 리전 전체 시도"),
    profile: str | None = Query(None, description="(개발용) 로컬 AWS 프로필명"),
):
    try:
        region_list = [r.strip() for r in regions.split(",")] if regions else None
        # ALLOWED_REGIONS가 설정되어 있으면 build_inventory 전에 필터링
        if region_list and _ALLOWED_REGIONS:
            region_list = [r for r in region_list if r in _ALLOWED_REGIONS]
        data = build_inventory(region_list, profile)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sagemaker catalog error: {e}")


# -----------------------------------------------------------------------------
# 1-2) Region Overview (Domains + Pipelines)
#      프론트 초기 로딩 시 한 번 호출해서
#      region → {domains[], pipelines[]} 구조를 한 번에 구성
# -----------------------------------------------------------------------------
@app.get("/sagemaker/overview")
def sagemaker_overview(
    regions: Optional[str] = Query(None, description="쉼표구분 리전 목록. 없으면 SageMaker 지원 리전 전체 스캔"),
    includeLatestExec: bool = Query(False, description="파이프라인별 최신 실행 요약 포함"),
    profile: Optional[str] = Query(None, description="(개발용) 로컬 AWS 프로필명. 운영에서는 미사용/무시 권장"),
):
    try:
        # 간단 캐시 키: regions|includeLatestExec|profile + ALLOWED_REGIONS 버전
        cache_key = f"regions={regions}|latest={includeLatestExec}|profile={bool(profile)}|allowed={','.join(sorted(_ALLOWED_REGIONS)) if _ALLOWED_REGIONS else ''}"
        now = int(time.time())
        if _OVERVIEW_TTL_SECONDS > 0:
            if _OVERVIEW_CACHE["data"] is not None and _OVERVIEW_CACHE["key"] == cache_key:
                if now - _OVERVIEW_CACHE["ts"] <= _OVERVIEW_TTL_SECONDS:
                    return _OVERVIEW_CACHE["data"]

        region_list = _parse_regions(regions, profile)
        out: List[Dict[str, Any]] = []

        for r in region_list:
            sess = boto3.session.Session(profile_name=profile, region_name=r) if profile \
                else boto3.session.Session(region_name=r)
            sm = sess.client("sagemaker", config=_BOTO_CFG)

            # 각 리전에서 도메인/파이프라인 조회
            try:
                domains = _list_all_domains(sm)
            except Exception as e:
                domains = []
            try:
                pipelines = _list_all_pipelines(sm, include_latest=includeLatestExec)
            except Exception as e:
                pipelines = []

            out.append({"region": r, "domains": domains, "pipelines": pipelines})

        data = {"regions": out}

        if _OVERVIEW_TTL_SECONDS > 0:
            _OVERVIEW_CACHE.update({"ts": now, "key": cache_key, "data": data})

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sagemaker overview error: {e}")


# -----------------------------------------------------------------------------
# 2) Lineage: 단건 조회
# -----------------------------------------------------------------------------
@app.get("/lineage")
def lineage_endpoint(
    pipeline: str = Query(..., description="SageMaker Pipeline Name"),
    region: str = Query(..., description="e.g., ap-northeast-2"),
    domain: str | None = Query(None, description="Optional SageMaker DomainName tag filter"),
    includeLatestExec: bool = Query(False, description="Include latest execution info"),
    profile: str | None = Query(None, description="Local dev only; AWS profile name"),
):
    try:
        data = get_lineage_json(
            region=region,
            pipeline_name=pipeline,
            domain_name=domain,
            include_latest_exec=includeLatestExec,
            profile=profile,
        )
        return data
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"internal error: {e}")


# -----------------------------------------------------------------------------
# 3) Lineage by Domain: 해당 도메인에 매칭되는 모든 파이프라인 일괄
# -----------------------------------------------------------------------------
@app.get("/lineage/by-domain")
def lineage_by_domain(
    region: str = Query(..., description="리전"),
    domain: str = Query(..., description="DomainName"),
    includeLatestExec: bool = Query(False),
    profile: str | None = Query(None),
):
    """
    해당 리전에서 DomainName 태그가 일치하는 파이프라인들을 전부 찾아
    각각의 라인리지를 수행해 한번에 반환
    """
    try:
        # /sagemaker/catalog 과 동일한 로직으로, 지정 리전만 카탈로그 생성
        inv = build_inventory([region], profile)
        region_block = next((r for r in inv["regions"] if r["region"] == region), None)
        if not region_block:
            raise HTTPException(status_code=404, detail=f"region not found: {region}")

        target_pipes: List[str] = []
        for p in region_block.get("pipelines", []):
            md = p.get("matchedDomain")
            # (1) 파이프라인 태그에 DomainName이 맞는지
            if md and md.get("DomainName") == domain:
                target_pipes.append(p["name"])
            # (2) 혹은 tags에 DomainName 직접 기입된 경우
            elif p.get("tags", {}).get("DomainName") == domain:
                target_pipes.append(p["name"])

        if not target_pipes:
            raise HTTPException(status_code=404, detail=f"no pipelines tagged with DomainName={domain} in {region}")

        results: List[Dict[str, Any]] = []
        for name in target_pipes:
            try:
                data = get_lineage_json(
                    region=region,
                    pipeline_name=name,
                    domain_name=domain,
                    include_latest_exec=includeLatestExec,
                    profile=profile,
                )
                results.append({"pipeline": name, "ok": True, "data": data})
            except Exception as e:
                results.append({"pipeline": name, "ok": False, "error": str(e)})

        return {
            "region": region,
            "domain": domain,
            "count": len(results),
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"by-domain error: {e}")


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # uvicorn 실행 옵션은 환경/도커에 맞게 조정하세요
    uvicorn.run(app, host="0.0.0.0", port=8000)
