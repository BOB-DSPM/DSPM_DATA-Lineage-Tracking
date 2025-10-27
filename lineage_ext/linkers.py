from typing import Dict, Any, List

def link_derivations(graph: Dict[str, Any]) -> None:
    """같은 URI가 어떤 스텝에서 write되고 다른 스텝에서 read되면 데이터→데이터 간 얕은 파생 관계 엣지 추가(옵션)."""
    writes = set(); reads = set()
    for e in graph.get("edges", []):
        if e.get("kind")=="write" and e["target"].startswith("data:"):
            writes.add(e["target"])
        if e.get("kind")=="read" and e["source"].startswith("data:"):
            reads.add(e["source"])
    for d in (writes & reads):
        # self-edge 대신 kind만 표시하거나, 필요시 실제 상류/하류 데이터 노드 식별해 연결
        graph["edges"].append({"id": f"derived:{d}", "source": d, "target": d, "kind":"derived"})

def link_deployments(graph: Dict[str, Any], catalog: Dict[str, Any]) -> None:
    """model ↔ endpoint/batch 연결 (카탈로그 기반, 필요 시 구현)."""
    # 스켈레톤. 프로젝트에 맞게 매핑 규칙을 채워넣으면 됨.
    return

def link_versions(graph: Dict[str, Any], catalog: Dict[str, Any]) -> None:
    """model/feature/dataset 버전 계보 연결 (카탈로그 기반)."""
    return
