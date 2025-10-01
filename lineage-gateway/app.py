# app.py
import os
import urllib.parse
from collections import deque
from typing import List, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware

# ---- Config ----
# 컨테이너 네트워크 기준 기본값. 로컬 단독 실행이면 http://localhost:5000/api/v1 로 바꿔도 됨.
MARQUEZ_API = os.getenv("MARQUEZ_API", "http://marquez:5000/api/v1")
# 이벤트 프록시가 사용할 엔드포인트 (미설정 시 /api/v1/lineage 로 폴백)
MARQUEZ_ENDPOINT = os.getenv("MARQUEZ_ENDPOINT", f"{MARQUEZ_API}/lineage")

app = FastAPI(title="DSPM Lineage Gateway", version="0.1.0")

# 프론트에서 직접 호출할 수 있도록 CORS (필요 시 도메인 제한)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# -----------------------------
# Events Proxy (OpenLineage 이벤트 → Marquez 전달)
# -----------------------------
@app.post("/events")
async def events_proxy(payload: Dict[str, Any] = Body(...)):
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(MARQUEZ_ENDPOINT, json=payload)
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return {"ok": True}
    except httpx.RequestError as e:
        raise HTTPException(502, f"upstream_unreachable: {e}") from e


# -----------------------------
# Runs (필수 쿼리 파라미터 job=namespace/name)
# -----------------------------
@app.get("/lineage/runs")
async def lineage_runs(
    job: str = Query(..., description="Target job as 'namespace/name' (URL-encoded OK)"),
    limit: int = Query(20, ge=1, le=500),
    since: str | None = Query(None, description="ISO time filter on createdAt (optional)"),
):
    """
    예) /lineage/runs?job=dspm.mlops%2Fjob.sample  또는  /lineage/runs?job=dspm.mlops/job.sample
    - 내부적으로는 Marquez 원 API: /api/v1/namespaces/{ns}/jobs/{name}/runs 호출
    """
    try:
        # URL 디코딩 및 유효성 검사
        job = urllib.parse.unquote(job).strip()

        # 과거 콜론 표기(dspm.mlops:job)도 느슨히 허용
        if "/" in job:
            ns, name = job.split("/", 1)
        elif ":" in job:
            ns, name = job.split(":", 1)
        else:
            raise HTTPException(422, "job must be 'namespace/name' (or 'namespace:name')")

        ns = ns.strip()
        name = name.strip()
        if not ns or not name:
            raise HTTPException(422, "namespace/name must be non-empty")

        url = f"{MARQUEZ_API}/namespaces/{urllib.parse.quote(ns)}/jobs/{urllib.parse.quote(name)}/runs"
        params = {"limit": limit}
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, params=params)

        if r.status_code >= 400:
            # 원문 에러를 그대로 전달 (4xx/5xx)
            raise HTTPException(r.status_code, r.text)

        data = r.json()
        runs = data.get("runs", data.get("data", []))
        if since:
            runs = [x for x in runs if x.get("createdAt", "") >= since]

        return {"job": f"{ns}/{name}", "count": len(runs), "runs": runs}

    except HTTPException:
        raise
    except Exception as e:
        # 내부 오류는 숨기지 말고 디버깅 가능한 메시지로 노출
        raise HTTPException(500, f"gateway_error: {e}") from e


# -----------------------------
# Lineage Graph (Marquez /lineage 프록시)
# -----------------------------
@app.get("/lineage/graph")
async def lineage_graph(entity: str, direction: str = "up", depth: int = 3):
    """
    entity: 노드 ID 문자열 (예: 'dspm.mlops/job.sample' 또는 'file::C:\\path\\file@sha256:...')
    direction: up|down  -> UPSTREAM / DOWNSTREAM
    """
    try:
        node_id = urllib.parse.quote(entity, safe="")
        dir_enum = "UPSTREAM" if direction.lower().startswith("up") else "DOWNSTREAM"
        url = f"{MARQUEZ_API}/lineage"
        params = {"nodeId": node_id, "direction": dir_enum, "depth": depth}

        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, params=params)

        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"gateway_error: {e}") from e


# -----------------------------
# Path (간단 BFS로 그래프에서 경로 찾기)
# -----------------------------
def _bfs_path(graph: Dict[str, Any], src: str, dst: str) -> List[str]:
    nodes = {n.get("id"): n for n in graph.get("graph", {}).get("nodes", [])}
    adj: Dict[str, List[str]] = {}
    for e in graph.get("graph", {}).get("edges", []):
        adj.setdefault(e.get("source"), []).append(e.get("target"))
    q = deque([src])
    prev = {src: None}
    while q:
        u = q.popleft()
        if u == dst:
            break
        for v in adj.get(u, []):
            if v not in prev:
                prev[v] = u
                q.append(v)
    if dst not in prev:
        return []
    path: List[str] = []
    cur = dst
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return list(reversed(path))


@app.get("/lineage/path")
async def lineage_path(frm: str, to: str, depth: int = 10):
    """
    frm → to 로의 경로를 DOWNSTREAM 먼저 탐색, 없으면 UPSTREAM에서 탐색
    """
    try:
        down = await lineage_graph(entity=frm, direction="down", depth=depth)
        path = _bfs_path(down, frm, to)
        if not path:
            up = await lineage_graph(entity=frm, direction="up", depth=depth)
            path = _bfs_path(up, frm, to)
        return {"from": frm, "to": to, "path": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"gateway_error: {e}") from e


# -----------------------------
# (옵션) 단순 목록 프록시: /lineage/jobs, /lineage/datasets
# -----------------------------
@app.get("/lineage/jobs")
async def lineage_jobs(namespace: str | None = None, limit: int = 50):
    try:
        params: Dict[str, Any] = {"limit": limit}
        if namespace:
            # 두 버전 API를 모두 시도 (마르케즈 버전에 따라 다름)
            url = f"{MARQUEZ_API}/jobs"
            params["namespace"] = namespace
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(url, params=params)
            if r.status_code == 404:
                # 네임스페이스 경로형으로 재시도
                url2 = f"{MARQUEZ_API}/namespaces/{urllib.parse.quote(namespace)}/jobs"
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(url2, params={"limit": limit})
        else:
            url = f"{MARQUEZ_API}/jobs"
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(url, params=params)

        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        data = r.json()
        rows = data.get("jobs", data.get("data", []))
        items = []
        for j in rows:
            ns = j.get("namespace") or (j.get("job") or {}).get("namespace")
            name = j.get("name") or (j.get("job") or {}).get("name")
            if ns and name:
                items.append({"entity": f"{ns}/{name}", "namespace": ns, "name": name})
        return {"count": len(items), "jobs": items}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"gateway_error: {e}") from e


@app.get("/lineage/datasets")
async def lineage_datasets(namespace: str | None = None, limit: int = 50):
    try:
        params: Dict[str, Any] = {"limit": limit}
        if namespace:
            # 두 버전 API를 모두 시도
            url = f"{MARQUEZ_API}/datasets"
            params["namespace"] = namespace
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(url, params=params)
            if r.status_code == 404:
                url2 = f"{MARQUEZ_API}/namespaces/{urllib.parse.quote(namespace)}/datasets"
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(url2, params={"limit": limit})
        else:
            url = f"{MARQUEZ_API}/datasets"
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(url, params=params)

        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        data = r.json()
        rows = data.get("datasets", data.get("data", []))
        items = []
        for d in rows:
            ns = d.get("namespace")
            name = d.get("name")
            if ns and name:
                items.append({"entity": f"{ns}/{name}", "namespace": ns, "name": name})
        return {"count": len(items), "datasets": items}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"gateway_error: {e}") from e
