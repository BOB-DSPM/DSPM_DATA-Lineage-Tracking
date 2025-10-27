from typing import Dict, Any, List, Tuple

Node = Dict[str, Any]
Edge = Dict[str, Any]
Graph = Dict[str, Any]

def norm_uri(u: str) -> str:
    return u.rstrip("/").lower() if isinstance(u, str) else u

def data_id(uri: str) -> str: return f"data:{norm_uri(uri)}"
def proc_id(step_id: str) -> str: return f"process:{step_id}"
