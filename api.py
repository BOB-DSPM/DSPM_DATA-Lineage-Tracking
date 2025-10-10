from __future__ import annotations

from typing import Optional, List, Dict, Any

import boto3
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from lineage import (
    get_lineage_json,           # 단건 라인리지 그래프 생성
    list_pipelines_with_domain, # 파이프라인 목록 + 태그/도메인 매핑
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
    - name, domainName, domainId 로 필터링 가능
    - includeLatestExec=true 이면 latestExecution 요약도 덧붙임
    """
    try:
        region_list = _parse_regions(regions, profile)
        out: List[Dict[str, Any]] = []

        for r in region_list:
            try:
                # 태그 + matchedDomain 포함하여 가져오기
                pipes = list_pipelines_with_domain(region=r, profile=profile)

                # 이름/도메인 필터
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

                # 최신 실행 1건 요약 추가
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
# 3) 도메인에 매칭되는 모든 파이프라인 라인리지
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
    (build_inventory 의존 제거 버전)
    """
    try:
        # 1) 해당 리전의 파이프라인 + 태그 조회
        pipes = list_pipelines_with_domain(region=region, profile=profile)

        # 2) DomainName=... 태그가 있는 파이프라인만 남기기
        targets = [
            p["name"] for p in pipes
            if (p.get("matchedDomain") and p["matchedDomain"].get("DomainName") == domain)
            or ((p.get("tags") or {}).get("DomainName") == domain)
        ]
        if not targets:
            raise HTTPException(status_code=404, detail=f"no pipelines tagged with DomainName={domain} in {region}")

        # 3) 각 파이프라인의 라인리지 생성
        results = []
        for name in targets:
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

        return {"region": region, "domain": domain, "count": len(results), "results": results}
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
