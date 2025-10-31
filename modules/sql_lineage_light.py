from __future__ import annotations
import re
from typing import Dict, Any, List

_INS_SEL = re.compile(r"insert\s+into\s+([a-zA-Z0-9_.]+).*?select\s+(.*?)\s+from\s+([a-zA-Z0-9_.]+)", re.IGNORECASE|re.DOTALL)
_CTAS   = re.compile(r"create\s+table\s+([a-zA-Z0-9_.]+)\s+as\s+select\s+(.*?)\s+from\s+([a-zA-Z0-9_.]+)", re.IGNORECASE|re.DOTALL)

def _split_cols(sel: str) -> List[str]:
    # 아주 단순 분할: 콤마 기준
    parts = [p.strip() for p in sel.split(",")]
    return [re.sub(r"\s+as\s+.*$", "", p, flags=re.IGNORECASE) for p in parts]

def extract_lineage(sql: str) -> Dict[str, Any]:
    """
    returns { "src_table":..., "dst_table":..., "cols":[ {"src":"a","dst":"a"}, ... ] }
    매우 단순화된 매핑. 실서비스는 sqlglot로 대체 권장.
    """
    m = _INS_SEL.search(sql) or _CTAS.search(sql)
    if not m:
        return {}
    dst, sel, src = m.group(1), m.group(2), m.group(3)
    cols = _split_cols(sel)
    # dst 컬럼명을 모르므로 src→dst 동일명 가정(추정)
    return {"src_table": src, "dst_table": dst, "cols": [{"src": c, "dst": c} for c in cols]}