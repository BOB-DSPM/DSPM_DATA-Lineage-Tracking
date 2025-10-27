import os
from dataclasses import dataclass

@dataclass
class ExtFlags:
    enable_glue: bool = False
    enable_registry: bool = False
    enable_endpoints: bool = False
    enable_feature_store: bool = False
    enable_pii: bool = False  # Macie/LF 등 추가 시

    @classmethod
    def from_env(cls) -> "ExtFlags":
        def on(k): return os.getenv(k, "false").lower() in ("1","true","yes","on")
        return cls(
            enable_glue=on("ENABLE_GLUE"),
            enable_registry=on("ENABLE_REGISTRY"),
            enable_endpoints=on("ENABLE_ENDPOINTS"),
            enable_feature_store=on("ENABLE_FEATURE_STORE"),
            enable_pii=on("ENABLE_PII"),
        )