from __future__ import annotations
from typing import Dict, Any, List

CONF_WEIGHT = {"High": 3, "Medium": 2, "Low": 1}

def combine_confidences(evidences: List[Dict[str, Any]]) -> str:
    if not evidences:
        return "Low"
    s = sum(CONF_WEIGHT.get(e.get("confidence","Low"), 1) for e in evidences)
    avg = s / len(evidences)
    if avg >= 2.5: return "High"
    if avg >= 1.5: return "Medium"
    return "Low"

def make_evidence(source: str, kind: str, locator: str, confidence: str="High") -> Dict[str, Any]:
    return {"source": source, "kind": kind, "locator": locator, "confidence": confidence}