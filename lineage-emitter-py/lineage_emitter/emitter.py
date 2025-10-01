SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"
PRODUCER   = "dspm-emitter/0.1.0"

import os, requests, uuid, datetime as dt

NAMESPACE = os.getenv("OL_NAMESPACE", "dspm.mlops")  # 팀 네임스페이스
MARQUEZ_ENDPOINT = os.getenv("MARQUEZ_ENDPOINT", "http://localhost:5000/api/v1/lineage")

def _event_time_utc():
    # RFC3339 with timezone (e.g., 2025-09-30T08:12:34.567890+00:00)
    return dt.datetime.now(dt.timezone.utc).isoformat()

def now_iso():
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).isoformat()

def emit(ev: dict):
    ev.setdefault("producer", PRODUCER)
    ev.setdefault("schemaURL", SCHEMA_URL)
    if "eventTime" not in ev:
        ev["eventTime"] = _event_time_utc()
    r = requests.post(MARQUEZ_ENDPOINT, json=ev, timeout=10)
    r.raise_for_status()
    return r.json() if r.text else {"ok": True}

def start_run(job_name: str, run_facets: dict=None, inputs=None):
    rid = str(uuid.uuid4())
    ev = {
        "eventType": "START",
        "eventTime": now_iso(),
        "run": {"runId": rid, "facets": run_facets or {}},
        "job": {"namespace": NAMESPACE, "name": job_name},
        "inputs": inputs or [],
        "outputs": []
    }
    emit(ev)
    return rid

def complete_run(run_id: str, job_name: str, outputs=None, run_facets: dict=None):
    ev = {
        "eventType": "COMPLETE",
        "eventTime": now_iso(),
        "run": {"runId": run_id, "facets": run_facets or {}},
        "job": {"namespace": NAMESPACE, "name": job_name},
        "inputs": [],
        "outputs": outputs or []
    }
    return emit(ev)
