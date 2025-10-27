from typing import Dict, Any, List, Tuple, Optional
import boto3
from .flags import ExtFlags

def _mk(sm_client=None, svc:str="sagemaker"):
    return sm_client or boto3.client(svc)

def collect_catalog(session, sm_client=None, flags:ExtFlags=None, base_graph=None) -> Dict[str, Any]:
    """환경 플래그에 따라 Glue/Registry/Endpoint/FeatureStore 메타를 느슨하게 수집."""
    flags = flags or ExtFlags()
    out: Dict[str, Any] = {}

    if flags.enable_glue:
        try:
            out["glue"] = {}  # 필요 시 Glue db/table를 base_graph의 S3 경로에서 유추
            # 최소 구현: 나중에 구체화(프로젝트 환경마다 다름)
        except Exception:
            pass

    if flags.enable_registry:
        try:
            sm = _mk(sm_client, "sagemaker")
            out["modelPackages"] = {}  # group -> [DescribeModelPackage ...]
            # 필요 시 pipeline 파라미터/태그에서 group name 읽어와 조회
        except Exception:
            pass

    if flags.enable_endpoints:
        try:
            sm = _mk(sm_client, "sagemaker")
            out["endpoints"] = {}  # name -> DescribeEndpoint + DescribeEndpointConfig
        except Exception:
            pass

    if flags.enable_feature_store:
        try:
            sm = _mk(sm_client, "sagemaker")
            out["featureGroups"] = {}  # name -> DescribeFeatureGroup
        except Exception:
            pass

    if flags.enable_pii:
        out["pii"] = {}  # Macie/LF 요약 결과(있다면)

    return out
