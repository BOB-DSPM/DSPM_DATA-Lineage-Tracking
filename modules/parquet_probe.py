from __future__ import annotations
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import pyarrow as pa            # noqa: F401 (type annotations, str(dtype) 용)
import pyarrow.parquet as pq
from pyarrow.fs import S3FileSystem

def _split_s3(s3_uri: str) -> tuple[str, str]:
    """s3://bucket/prefix -> (bucket, key)"""
    p = urlparse(s3_uri)
    return p.netloc, p.path.lstrip("/")

def is_parquet_uri(uri: str) -> bool:
    return uri.lower().endswith(".parquet")

def parquet_schema_from_s3(s3_uri: str, region: Optional[str] = None) -> Dict[str, Any]:
    """
    Parquet 메타에서 스키마를 추출해 통일된 포맷으로 반환.
    반환 형태는 schema_sampler가 쓰는 dict와 동일하게 맞춤.
    """
    bucket, key = _split_s3(s3_uri)
    fs = S3FileSystem(region=region) if region else S3FileSystem()

    # 파일 하나만 열어도 스키마는 동일
    pf = pq.ParquetFile(f"s3://{bucket}/{key}", filesystem=fs)
    arrow_schema: pa.Schema = pf.schema_arrow

    fields: Dict[str, str] = {}
    for f in arrow_schema:
        # 예: int64, string, timestamp[us, tz=UTC] 등
        fields[f.name] = str(f.type)

    return {
        "ok": True,
        "dataset_id": f"s3://{bucket}/{key}",
        "schema": {
            "format": "parquet",
            "fields": fields,
            "sampled_files": [f"s3://{bucket}/{key}"],
            "meta": {
                "num_row_groups": pf.num_row_groups,
            },
        },
    }
