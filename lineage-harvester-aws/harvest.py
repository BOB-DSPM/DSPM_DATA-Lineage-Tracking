import os, argparse, uuid, requests
from datetime import datetime, timezone

NAMESPACE = os.getenv("OL_NAMESPACE", "dspm.mlops")
MARQUEZ_ENDPOINT = os.getenv("MARQUEZ_ENDPOINT", "http://localhost:5000/api/v1/lineage")

def now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def emit(event: dict):
    r = requests.post(MARQUEZ_ENDPOINT, json=event, timeout=5)
    r.raise_for_status()

def emit_train_job(job_id, dataset_uri, algorithm, model_name, model_ver, status):
    rid = str(uuid.uuid4())
    start = {
        "eventType": "START",
        "eventTime": now_iso(),
        "run": {"runId": rid, "facets": {"sagemaker": {"jobId": job_id, "algorithm": algorithm}}},
        "job": {"namespace": NAMESPACE, "name": "train_model"},
        "inputs": [{"namespace": "s3", "name": dataset_uri}],
        "outputs": []
    }
    emit(start)
    complete = {
        "eventType": "COMPLETE",
        "eventTime": now_iso(),
        "run": {"runId": rid, "facets": {"modelRegistry": {"name": model_name, "version": model_ver, "status": status}}},
        "job": {"namespace": NAMESPACE, "name": "train_model"},
        "inputs": [],
        "outputs": [{"namespace": "ml-registry", "name": f"model:{model_name}/{model_ver}"}]
    }
    emit(complete)

def dataset_id_for_local(path: str) -> str:
    import hashlib, pathlib
    p = pathlib.Path(path).resolve()
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return f"file::{p}@sha256:{h.hexdigest()}"

def mock_once():
    #dataset_uri = "s3://YOUR_BUCKET/datasets/train@sha256:DEMOHASH"  # 사용할 S3 버킷의 경로 및 해시로 변경 필요
    local_path = os.getenv("LOCAL_TRAIN_FILE", "testdata/ml/train.csv")  # ← 레포 상대경로
    dataset_uri = dataset_id_for_local(local_path)
    emit_train_job("train-LOCAL-001", dataset_uri, "xgboost", "fraud", "1.2.0", "Approved")

def real_fetch(limit=3):
    import boto3
    sm = boto3.client("sagemaker")
    jobs = sm.list_training_jobs(SortBy="CreationTime", SortOrder="Descending", MaxResults=limit)["TrainingJobSummaries"]
    for j in jobs:
        job_name = j["TrainingJobName"]
        dj = sm.describe_training_job(TrainingJobName=job_name)
        # 입력 데이터셋(첫 채널 가정—환경에 맞게 수정)
        s3uri = dj["InputDataConfig"][0]["DataSource"]["S3DataSource"]["S3Uri"]
        alg_img = dj["AlgorithmSpecification"].get("TrainingImage","")
        algorithm = alg_img.split("/")[-1] if alg_img else dj["AlgorithmSpecification"].get("AlgorithmName","unknown")
        # 모델/버전/상태는 조직별 규약에 맞게 매핑(예시)
        model_name, model_ver, status = job_name + "-model", "1.0.0", "Approved"
        # 필요 시 해시 버전 표기(체크섬)로 변환
        emit_train_job(job_name, s3uri, algorithm, model_name, model_ver, status)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock","real"], default="mock")
    ap.add_argument("--limit", type=int, default=3)
    args = ap.parse_args()
    if args.mode == "mock":
        mock_once()
    else:
        real_fetch(limit=args.limit)
