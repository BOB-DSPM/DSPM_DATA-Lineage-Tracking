from __future__ import annotations
import json, os, time
from typing import Dict, Any, Iterable, List, Optional

STORE = os.getenv("SQL_LINEAGE_STORE", "data/sql_lineage.jsonl")

def _ensure_dir():
    d = os.path.dirname(STORE) or "."
    os.makedirs(d, exist_ok=True)

def put(record: Dict[str, Any]) -> None:
    _ensure_dir()
    rec = {**record, "ts": record.get("ts") or int(time.time())}
    with open(STORE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def _read_all() -> Iterable[Dict[str, Any]]:
    if not os.path.exists(STORE):
        return []
    with open(STORE, "r", encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try: yield json.loads(line)
            except Exception: continue

def get_by_job(job_id: str) -> List[Dict[str, Any]]:
    return [r for r in _read_all() if r.get("job_id")==job_id]

def get_by_pipeline(pipeline: str) -> List[Dict[str, Any]]:
    return [r for r in _read_all() if r.get("pipeline")==pipeline]

def latest_by_step(pipeline: str, step: str) -> Optional[Dict[str, Any]]:
    rows = [r for r in _read_all() if r.get("pipeline")==pipeline and r.get("step")==step]
    rows.sort(key=lambda x: x.get("ts",0), reverse=True)
    return rows[0] if rows else None
