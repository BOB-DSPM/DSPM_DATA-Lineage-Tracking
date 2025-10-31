# modules/sql_try.py
from __future__ import annotations
from typing import Optional, Dict, Any, List, Set

# 1) 우선, 있으면 기존 경량 파서의 parse_sql을 사용
_PARSE_SQL_LIGHT = None
try:
    from modules.sql_lineage_light import parse_sql as _PARSE_SQL_LIGHT  # type: ignore
except Exception:
    _PARSE_SQL_LIGHT = None


def _normalize_table_name(table) -> str:
    """
    sqlglot exp.Table -> "catalog.db.table" or "db.table" or "table"
    """
    # table.name, table.db, table.catalog are safe on sqlglot>=11
    parts = []
    try:
        if getattr(table, "catalog", None):
            parts.append(table.catalog)
        if getattr(table, "db", None):
            parts.append(table.db)
        if getattr(table, "name", None):
            parts.append(table.name)
    except Exception:
        # very defensive
        pass
    if not parts and hasattr(table, "this"):
        # older versions might keep identifier under .this
        try:
            parts.append(table.this.name)
        except Exception:
            pass
    return ".".join([p for p in parts if p])


def _parse_with_sqlglot(sql: str, dialect: Optional[str]) -> Dict[str, Any]:
    """
    Fallback parser using sqlglot (CREATE TABLE AS SELECT / INSERT INTO ... SELECT ...  위주)
    반환 형식: {"ok": bool, "dst": str|None, "sources": [str], "columns": [str], "note": str|None}
    """
    try:
        import sqlglot
        from sqlglot import parse_one, exp
    except Exception as e:
        return {"ok": False, "error": f"sqlglot not installed: {e}"}

    try:
        expr = parse_one(sql, read=dialect) if dialect else parse_one(sql)
    except Exception as e:
        return {"ok": False, "error": f"parse error: {e}"}

    dst: Optional[str] = None
    sources: Set[str] = set()
    columns: List[str] = []

    # 1) 대상 테이블(dst)
    #   - CREATE ... TABLE x AS SELECT ...
    create = expr.find(exp.Create)
    if create and isinstance(create.this, exp.Table):
        dst = _normalize_table_name(create.this)

    #   - INSERT INTO x SELECT ...
    ins = expr.find(exp.Insert)
    if not dst and ins and isinstance(ins.this, exp.Table):
        dst = _normalize_table_name(ins.this)

    # 2) 소스 테이블(sources): 모든 exp.Table 에서 dst 는 제외
    for t in expr.find_all(exp.Table):
        name = _normalize_table_name(t)
        if name and name != dst:
            sources.add(name)

    # 3) 컬럼: SELECT 리스트의 alias/name 추출
    sel = expr.find(exp.Select)
    if sel:
        for p in sel.expressions:  # projection list
            # alias 우선, 없으면 컬럼/식 문자열
            alias = getattr(p, "alias", None)
            alias_name = None
            try:
                if alias and getattr(alias, "name", None):
                    alias_name = alias.name
            except Exception:
                pass
            if alias_name:
                columns.append(alias_name)
            else:
                # 식을 텍스트로
                try:
                    columns.append(p.sql(dialect=dialect) if dialect else p.sql())
                except Exception:
                    columns.append(str(p))

    ok = bool(dst or sources)
    return {
        "ok": ok,
        "dst": dst,
        "sources": sorted(list(sources)),
        "columns": columns,
        "note": "fallback(sqlglot) used" if _PARSE_SQL_LIGHT is None else None,
    }


def try_parse(sql: str, dialect: Optional[str] = None) -> Dict[str, Any]:
    """
    통합 래퍼:
    - modules.sql_lineage_light.parse_sql 이 있으면 그걸 호출(기존 동작 유지)
    - 없으면 sqlglot 기반 폴백 파서 사용
    """
    if _PARSE_SQL_LIGHT is not None:
        try:
            ok, dst, sources, columns, note = _PARSE_SQL_LIGHT(sql, dialect=dialect)  # type: ignore
            return {
                "ok": bool(ok),
                "dst": dst,
                "sources": sources or [],
                "columns": columns or [],
                "note": note,
            }
        except Exception as e:
            # 경량 파서 실패 시에도 폴백 시도
            fb = _parse_with_sqlglot(sql, dialect)
            fb["note"] = f"light parser failed: {e}; fallback used"
            return fb
    # 경량 파서 없음 → 바로 폴백
    return _parse_with_sqlglot(sql, dialect)
