# api.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from lineage import get_lineage_json  # lineage.py에서 만든 함수

app = FastAPI(title="SageMaker Lineage API", version="1.0.0")

# (필요 시) CORS 허용 - 프론트에서 직접 콜할 경우 사용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 운영에서는 특정 도메인으로 제한 권장
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

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
        # 파이프라인 못 찾는 등 사용자 오류
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        # 내부 오류
        raise HTTPException(status_code=500, detail=f"internal error: {e}")

if __name__ == "__main__":
    # 로컬 실행
    uvicorn.run(app, host="0.0.0.0", port=8000)
