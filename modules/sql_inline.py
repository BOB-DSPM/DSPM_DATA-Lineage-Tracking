# modules/sql_inline.py
from __future__ import annotations
from typing import Dict, List, Optional
from datetime import datetime
from modules.sql_collector import try_parse  # sql_collector가 내부에서 try_parse 호출하는 구조면 교체

def collect_inline_sql(sql_list: List[str], dialect: Optional[str]=None) -> List[Dict]:
    """
    메모리 내 SQL 문자열을 직접 파싱하여 표준 레코드 리스트로 반환.
    file 필드는 inline::<index> 로 표기.
    """
    results=[]
    ts=int(datetime.utcnow().timestamp())
    for i, sql in enumerate(sql_list, start=1):
        if not sql or len(sql.strip()) < 10:
            continue
        res = try_parse(sql, dialect=dialect)
        if res.get("ok") and (res.get("dst") or res.get("sources")):
            results.append({
                "file": f"inline::{i}",
                "sql": sql,
                "dst": res.get("dst"),
                "sources": res.get("sources", []),
                "columns": res.get("columns", []),
                "ts": ts
            })
    return results