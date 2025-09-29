import hashlib
import pathlib
import os, re, uuid, requests
from datetime import datetime, timezone
from fastapi import FastAPI, Body

# === 환경 따라 수정 ===
NAMESPACE = os.getenv("OL_NAMESPACE", "dspm.mlops")
MARQUEZ_ENDPOINT = os.getenv("MARQUEZ_ENDPOINT", "http://localhost:5000/api/v1/lineage")
TESTDATA_DIR = os.getenv("TESTDATA_DIR", "/data/test")

def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def dataset_id_for_local(path: str) -> str:
    p = pathlib.Path(path).resolve()
    return f"file::{p}@sha256:{sha256_of(str(p))}"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
RRN_RE   = re.compile(r"\\b\\d{6}-\\d{7}\\b")  # 주민번호 예시

app = FastAPI(title="DSPM PII Scanner")

def now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def emit(event: dict):
    r = requests.post(MARQUEZ_ENDPOINT, json=event, timeout=5)
    r.raise_for_status()

@app.post("/scan")
def scan(payload: dict = Body(...)):
    # payload 예:
    # { "path": "pii/rrn_mix.csv" }  # 레포 상대경로
    path   = payload.get("path")
    target = payload.get("target")   # s3::... 형태(옵션)
    text   = payload.get("text")     # 직접 본문(옵션, 데모용)

    if path:
        rp = path.replace("testdata/","") if path.startswith("testdata/") else path
        full = os.path.join(TESTDATA_DIR, rp)
        if not os.path.exists(full):
            return {"error": f"file not found: {full}"}
        with open(full, "r", encoding="utf-8") as f:
            text = f.read()
        target = dataset_id_for_local(full)    # ← 로컬 파일을 데이터셋 ID로

    # TODO: 실제 사용 시 S3에서 파일을 다운/스트림해서 검사
    dpi = []
    if EMAIL_RE.search(text): dpi.append("email")
    if RRN_RE.search(text):   dpi.append("rrn")
    risk = "high" if "rrn" in dpi else ("medium" if "email" in dpi else "none")

    rid = str(uuid.uuid4())
    start = {
        "eventType":"START","eventTime": now_iso(),
        "run":{"runId":rid,"facets":{}},
        "job":{"namespace":NAMESPACE,"name":"pii_scan"},
        "inputs":[{"namespace":"s3","name":target}],
        "outputs":[]
    }
    emit(start)
    complete = {
        "eventType":"COMPLETE","eventTime": now_iso(),
        "run":{"runId":rid,"facets":{"pii":{"dpi":dpi,"risk":risk}}},
        "job":{"namespace":NAMESPACE,"name":"pii_scan"},
        "inputs":[],
        "outputs":[{"namespace":"s3","name":target}]
    }
    emit(complete)
    return {"target": target, "dpi": dpi, "risk": risk}
