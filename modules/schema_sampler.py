from __future__ import annotations
import io, json, csv, re
from typing import Dict, Any, List, Tuple
from modules.parquet_probe import is_parquet_uri, parquet_schema_from_s3
import boto3
from botocore.client import Config

try:
    import pyarrow.parquet as pq  # 선택
    _HAS_PQ = True
except Exception:
    _HAS_PQ = False

_S3_RE = re.compile(r"^s3://([^/]+)/?(.*)$")

def parse_s3_uri(uri: str) -> Tuple[str, str]:
    m = _S3_RE.match(uri)
    if not m:
        raise ValueError(f"invalid s3 uri: {uri}")
    return m.group(1), m.group(2)

def list_objects_sample(s3, bucket: str, prefix: str, max_objects: int=5) -> List[Dict[str, Any]]:
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for c in page.get("Contents", []):
            key = c.get("Key")
            if key and not key.endswith("/"):
                out.append({"Key": key, "Size": c.get("Size")})
                if len(out) >= max_objects:
                    return out
    return out

def _detect_type_from_name(name: str) -> str:
    n = name.lower()
    if n.endswith(".json") or n.endswith(".jsonl"):
        return "json"
    if n.endswith(".csv"):
        return "csv"
    if n.endswith(".parquet") or n.endswith(".pq"):
        return "parquet"
    return "unknown"

def _read_head(s3, bucket: str, key: str, max_bytes: int=256*1024) -> bytes:
    rng = f"bytes=0-{max_bytes-1}"
    r = s3.get_object(Bucket=bucket, Key=key, Range=rng)
    return r["Body"].read()

def _schema_from_json(buf: bytes) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"fields": {}}
    # JSONL/JSON 둘 다 지원: 라인별 파싱 → dict만 취합
    lines = buf.decode("utf-8", errors="ignore").splitlines()
    cnt = 0
    for ln in lines[:1000]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    t = type(v).__name__
                    schema["fields"].setdefault(k, {"types": set(), "nulls": 0})
                    schema["fields"][k]["types"].add(t)
                cnt += 1
        except Exception:
            # 비-JSONL이면 통째로 시도
            try:
                obj = json.loads("\n".join(lines))
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        t = type(v).__name__
                        schema["fields"].setdefault(k, {"types": set(), "nulls": 0})
                        schema["fields"][k]["types"].add(t)
                    cnt += 1
            except Exception:
                pass
        if cnt >= 50:
            break
    # set → list
    for k in list(schema["fields"].keys()):
        schema["fields"][k]["types"] = sorted(list(schema["fields"][k]["types"]))
    schema["sampled_rows"] = cnt
    schema["format"] = "json"
    return schema

def _schema_from_csv(buf: bytes) -> Dict[str, Any]:
    s = buf.decode("utf-8", errors="ignore")
    reader = csv.reader(io.StringIO(s))
    rows = list(reader)[:500]
    if not rows:
        return {"fields": {}, "sampled_rows": 0, "format": "csv"}
    headers = rows[0]
    fields = {h: {"types": set()} for h in headers}
    for r in rows[1:]:
        for i, h in enumerate(headers):
            if i >= len(r): 
                continue
            val = r[i]
            t = "null" if val=="" else _guess_type(val)
            fields[h]["types"].add(t)
    for h in headers:
        fields[h]["types"] = sorted(list(fields[h]["types"]))
    return {"fields": fields, "sampled_rows": max(0, len(rows)-1), "format": "csv"}

def _guess_type(v: str) -> str:
    v = v.strip()
    if not v:
        return "null"
    try:
        int(v); return "int"
    except: pass
    try:
        float(v); return "float"
    except: pass
    if v.lower() in ("true","false"): return "bool"
    return "string"

def _schema_from_parquet(buf: bytes) -> Dict[str, Any]:
    if not _HAS_PQ:
        return {"fields": {}, "sampled_rows": 0, "format": "parquet", "note":"pyarrow not installed"}
    bio = io.BytesIO(buf)
    pf = pq.ParquetFile(bio)
    sch = pf.schema_arrow
    fields = {}
    for f in sch:
        fields[f.name] = {"types": [str(f.type)]}
    return {"fields": fields, "sampled_rows": 0, "format": "parquet"}

def sample_schema(region: str, s3_uri: str, max_objects: int=5, max_bytes: int=256*1024) -> Dict[str, Any]:
    """s3://... prefix에서 일부 객체의 head만 읽어 스키마 추출"""
    s3 = boto3.client("s3", region_name=region, config=Config(retries={"max_attempts": 5}))
    bucket, prefix = parse_s3_uri(s3_uri)
    objs = list_objects_sample(s3, bucket, prefix, max_objects=max_objects)

    merged: Dict[str, Any] = {"format": None, "fields": {}, "sampled_files": []}

    for o in objs:
        key = o["Key"]
        obj_uri = f"s3://{bucket}/{key}"
        ftype = _detect_type_from_name(key)

        try:
            # 1) Parquet이면: pyarrow로 S3에서 메타 스키마 직접 추출(정확)
            if ftype == "parquet":
                rec = parquet_schema_from_s3(obj_uri, region=region)   # ← 새 경로
                sc = rec["schema"]

            else:
                # 2) 그 외(JSON/CSV): 기존처럼 head 일부만 읽어서 샘플링
                head = _read_head(s3, bucket, key, max_bytes=max_bytes)
                if ftype == "json":
                    sc = _schema_from_json(head)
                elif ftype == "csv":
                    sc = _schema_from_csv(head)
                else:
                    # 기타 포맷은 스킵
                    continue

            # -- 공통 머지 로직 --
            for k, meta in sc.get("fields", {}).items():
                merged["fields"].setdefault(k, {"types": set()})
                for t in meta.get("types", []):
                    merged["fields"][k]["types"].add(t)

            merged["sampled_files"].append({"key": key, "format": sc.get("format")})
            if not merged["format"]:
                merged["format"] = sc.get("format")

        except Exception as e:
            print(f"[schema] skip {obj_uri}: {e}")
            continue

    # set → list
    for k in list(merged["fields"].keys()):
        merged["fields"][k]["types"] = sorted(list(merged["fields"][k]["types"]))

    return merged