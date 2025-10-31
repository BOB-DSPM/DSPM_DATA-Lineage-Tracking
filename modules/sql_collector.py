from __future__ import annotations
import os, re, glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from modules.sql_try import try_parse

SQL_EXT = (".sql",)
DBT_GLOBS = ["models/**/*.sql", "seeds/**/*.sql"]

# 간단한 임베디드 SQL 탐지 정규식(필요 시 강화 가능)
SQL_REGEX = re.compile(
    r"(create\s+table.*?as\s+select|insert\s+into\s+.*?select).*",
    re.IGNORECASE | re.DOTALL,
)

SQL_GLOB_PATTERNS = ["**/*.sql", "models/**/*.sql", "seeds/**/*.sql"]

PY_SQL_REGEX = re.compile(
    r"""(?P<sql>
        CREATE\s+TABLE\s+.+?\s+AS\s+SELECT.+?;|
        INSERT\s+INTO\s+.+?\s+SELECT.+?;
    )""",
    re.IGNORECASE | re.DOTALL,
)

def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def collect_from_repo(repo_path: str) -> List[Dict[str, Any]]:
    """레포 경로에서 .sql, dbt 폴더, .py 임베디드 SQL을 수집해 원문 SQL 리스트로 반환"""
    items: List[Dict[str, Any]] = []

    # 1) 일반 .sql
    for p in glob.glob(os.path.join(repo_path, "**", "*"), recursive=True):
        if p.lower().endswith(SQL_EXT):
            s = _read(p).strip()
            if s:
                items.append({"file": p, "sql": s})

    # 2) dbt 전용 경로
    for pattern in DBT_GLOBS:
        for p in glob.glob(os.path.join(repo_path, pattern), recursive=True):
            s = _read(p).strip()
            if s:
                items.append({"file": p, "sql": s})

    # 3) .py 임베디드 SQL(라이트 탐지)
    for p in glob.glob(os.path.join(repo_path, "**", "*.py"), recursive=True):
        text = _read(p)
        m = SQL_REGEX.search(text)
        if m:
            items.append({"file": p, "sql": m.group(0)})

    return items

def _read_text(fp: Path) -> str:
    try:
        return fp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

def _scan_sql_files(root: Path) -> List[Tuple[Path, str]]:
    out = []
    for pat in SQL_GLOB_PATTERNS:
        for fp in root.glob(pat):
            if fp.is_file():
                s = _read_text(fp).strip()
                if s:
                    out.append((fp, s))
    return out

def _scan_python_embedded_sql(root: Path) -> List[Tuple[Path, str]]:
    out = []
    for fp in root.glob("**/*.py"):
        if not fp.is_file():
            continue
        text = _read_text(fp)
        if not text:
            continue
        for m in PY_SQL_REGEX.finditer(text):
            sql = m.group("sql")
            if sql and len(sql) >= 20:
                out.append((fp, sql))
    return out

def collect_sql(root: Path, dialect: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    root 폴더에서 SQL/임베디드 SQL 수집 → sqlglot(try_parse)로 파싱 → 요약 리스트 반환
    반환 항목: file, sql, dst, sources[], columns[], ts
    """
    results: List[Dict[str, Any]] = []
    ts = int(datetime.utcnow().timestamp())

    def _append(fp: Path, sql: str):
        res = try_parse(sql, dialect=dialect)
        if res.get("ok") and (res.get("dst") or res.get("sources")):
            results.append({
                "file": str(fp),
                "sql": sql,
                "dst": res.get("dst"),
                "sources": res.get("sources", []),
                "columns": res.get("columns", []),
                "ts": ts,
            })

    for fp, sql in _scan_sql_files(root):
        _append(fp, sql)

    for fp, sql in _scan_python_embedded_sql(root):
        _append(fp, sql)

    return results
