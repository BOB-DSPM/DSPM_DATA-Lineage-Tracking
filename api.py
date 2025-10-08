# api.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from lineage import (
    get_lineage_json,
    build_inventory,          # NEW
    get_available_regions,    # NEW
    list_pipelines_with_domain,  # (선택) 필요 시 사용
)

app = FastAPI(title="SageMaker Lineage API", version="1.1.0")

# CORS (운영에서는 특정 도메인으로 제한 권장)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# -----------------------------
# 0) Health Check
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}

# -----------------------------
# 1) Inventory: Region→Domain→Pipeline
# -----------------------------
@app.get("/inventory")
def inventory(
    regions: str | None = Query(None, description="쉼표구분 리전 목록. 미지정 시 SageMaker 지원 리전 전체"),
    profile: str | None = Query(None, description="(개발용) 로컬 AWS 프로필명"),
):
    try:
        region_list = [r.strip() for r in regions.split(",")] if regions else None
        data = build_inventory(region_list, profile)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"inventory error: {e}")

# -----------------------------
# 2) Lineage: 기존 단건 조회
# -----------------------------
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

# -----------------------------
# 3) Lineage by Domain: 선택한 도메인에 매칭되는 모든 파이프라인 일괄
# -----------------------------
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
        # 인벤토리에서 해당 도메인과 매칭된 파이프라인만 추출
        inv = build_inventory([region], profile)
        region_block = next((r for r in inv["regions"] if r["region"] == region), None)
        if not region_block:
            raise HTTPException(status_code=404, detail=f"region not found: {region}")

        target_pipes = []
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

        results = []
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
