from typing import Dict, Any, List
from .types import Graph, Node, Edge, data_id, proc_id, norm_uri
from .flags import ExtFlags
from .linkers import link_derivations, link_deployments, link_versions

def _ensure_data_node(nodes:Dict[str,Node], uri:str, meta:Dict[str,Any]=None)->Node:
    nid = data_id(uri)
    if nid not in nodes:
        nodes[nid] = {"id": nid, "type":"dataArtifact", "label": uri, "uri": uri, "meta": meta or {}}
    return nodes[nid]

def build_data_view_graph_ext(graph_pipeline: Graph, catalog: Dict[str,Any], flags: ExtFlags) -> Graph:
    """기존 파이프라인 그래프를 데이터-중심 이분그래프로 변환 + 확장 엔티티 연결."""
    data_nodes: Dict[str, Node] = {}
    proc_nodes: List[Node] = []
    edges: List[Edge] = []

    # 1) 프로세스 노드
    for n in graph_pipeline.get("nodes", []):
        pid = proc_id(n["id"])
        proc_nodes.append({
            "id": pid, "type":"processNode", "label": n.get("label") or n["id"],
            "stepId": n["id"], "stepType": n.get("type"), "run": n.get("run"),
        })

        for i in n.get("inputs", []):
            u = i.get("uri");  if not u: continue
            dn = _ensure_data_node(data_nodes, u)
            edges.append({"id": f"e:{dn['id']}->{pid}:read", "source": dn["id"], "target": pid, "kind":"read"})
        for o in n.get("outputs", []):
            u = o.get("uri");  if not u: continue
            dn = _ensure_data_node(data_nodes, u)
            edges.append({"id": f"e:{pid}->{dn['id']}:write", "source": pid, "target": dn["id"], "kind":"write"})

    # 2) 기존 artifacts의 S3 메타 병합
    by_uri = { a.get("uri"): a for a in graph_pipeline.get("artifacts", []) if a.get("uri") }
    for uri, node in data_nodes.items():
        u = node["uri"]
        if u in by_uri and by_uri[u].get("s3"):
            node.setdefault("meta", {})["s3"] = by_uri[u]["s3"]

    # 3) 확장 엔티티(선택)
    nodes_extra: List[Node] = []
    if flags.enable_registry:
        # TODO: catalog["modelPackages"]에서 modelPackage 노드 생성
        pass
    if flags.enable_endpoints:
        # TODO: catalog["endpoints"]에서 endpoint 노드 생성
        pass
    if flags.enable_feature_store:
        # TODO: catalog["featureGroups"]에서 featureGroup 노드 생성
        pass

    graph: Graph = {
        "nodes": list(data_nodes.values()) + proc_nodes + nodes_extra,
        "edges": edges
    }

    # 4) 관계 후처리(파생/버전/배포)
    link_derivations(graph)
    if flags.enable_registry or flags.enable_endpoints:
        link_deployments(graph, catalog)
    if flags.enable_registry or flags.enable_feature_store:
        link_versions(graph, catalog)

    return graph
