import os, urllib.parse
from collections import deque
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
import httpx

MARQUEZ_API = os.getenv("MARQUEZ_API", "http://localhost:5000/api/v1")

app = FastAPI(title="DSPM Lineage Gateway", version="0.1.0")

# 프론트에서 직접 호출할 수 있도록 CORS (필요 시 제한)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.post("/events")
async def events_proxy(payload: dict = Body(...)):
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.post(f"{MARQUEZ_API}/lineage", json=payload)
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Marquez error: {r.text}")
    return {"ok": True}

@app.get("/lineage/runs")
async def lineage_runs(job: str, since: str | None = None):
    # job 예: "dspm.mlops:train_model" 또는 "train_model"(네임스페이스 생략)
    if ":" in job:
        ns, name = job.split(":", 1)
    else:
        ns, name = os.getenv("OL_NAMESPACE", "dspm.mlops"), job
    url = f"{MARQUEZ_API}/jobs/{urllib.parse.quote(ns)}/{urllib.parse.quote(name)}/runs"
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Marquez error: {r.text}")
    data = r.json()
    runs = data.get("runs", [])
    if since:
        runs = [x for x in runs if x.get("createdAt","") >= since]
    return {"job": f"{ns}:{name}", "runs": runs}

@app.get("/lineage/graph")
async def lineage_graph(entity: str, direction: str="up", depth: int=3):
    # entity 예: "ml-registry::model:fraud/1.2.0" 또는 "s3::s3://bucket/key@sha256:..."
    node_id = urllib.parse.quote(entity, safe="")
    d = "UPSTREAM" if direction == "up" else "DOWNSTREAM"
    url = f"{MARQUEZ_API}/lineage?nodeId={node_id}&direction={d}&depth={depth}"
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Marquez error: {r.text}")
    return r.json()

def _bfs_path(graph: dict, src: str, dst: str) -> list[str]:
    nodes = {n["id"]: n for n in graph.get("graph",{}).get("nodes",[])}
    adj   = {}
    for e in graph.get("graph",{}).get("edges",[]):
        adj.setdefault(e["source"], []).append(e["target"])
    q = deque([src]); prev = {src: None}
    while q:
        u = q.popleft()
        if u == dst: break
        for v in adj.get(u, []):
            if v not in prev:
                prev[v] = u
                q.append(v)
    if dst not in prev: return []
    path = []; cur = dst
    while cur is not None:
        path.append(cur); cur = prev[cur]
    return list(reversed(path))

@app.get("/lineage/path")
async def lineage_path(frm: str, to: str, depth: int=10):
    down = await lineage_graph(entity=frm, direction="down", depth=depth)
    path = _bfs_path(down, frm, to)
    if not path:
        up = await lineage_graph(entity=frm, direction="up", depth=depth)
        path = _bfs_path(up, frm, to)
    return {"from": frm, "to": to, "path": path}
