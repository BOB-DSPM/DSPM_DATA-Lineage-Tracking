from __future__ import annotations
import json, os, hashlib, time
from typing import Dict, Any, List, Optional, Tuple

_STORE_DIR = os.getenv("SCHEMA_STORE_DIR", "./data")
_STORE_FILE = os.path.join(_STORE_DIR, "schema_store.jsonl")

def _ensure_dir():
    os.makedirs(_STORE_DIR, exist_ok=True)

def _policy_hash(policy: Dict[str, Any]) -> str:
    h = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:16]
    return h

def dataset_id_from_s3(bucket: str, prefix: str) -> str:
    # 버킷/프리픽스 기반 간단 ID
    p = prefix.strip("/").replace("/", "::")
    return f"s3://{bucket}/{p}"

def save_schema(dataset_id: str, schema: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    """스키마 버전 append 저장"""
    _ensure_dir()
    rec = {
        "dataset_id": dataset_id,
        "version": int(time.time()),  # 간단 버저닝(타임스탬프)
        "policy_hash": _policy_hash(policy),
        "policy": policy,
        "schema": schema,
        "sampled_at": int(time.time()),
    }
    with open(_STORE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec

def list_versions(dataset_id: str) -> List[Dict[str, Any]]:
    if not os.path.exists(_STORE_FILE):
        return []
    out: List[Dict[str, Any]] = []
    with open(_STORE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                o = json.loads(line)
                if o.get("dataset_id") == dataset_id:
                    out.append(o)
            except Exception:
                pass
    # 최신이 앞으로 오게 정렬
    out.sort(key=lambda r: r.get("version", 0), reverse=True)
    return out

def get_version(dataset_id: str, version: Optional[int]=None) -> Optional[Dict[str, Any]]:
    vers = list_versions(dataset_id)
    if not vers:
        return None
    if version is None:
        return vers[0]
    for v in vers:
        if v.get("version") == version:
            return v
    return None