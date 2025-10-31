from __future__ import annotations
from typing import Dict, Any, List, Optional
import boto3
from botocore.config import Config

_BOTO_CFG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

def describe_feature_group(region: str, name: str, profile: Optional[str]=None) -> Dict[str, Any]:
    sess = boto3.session.Session(profile_name=profile, region_name=region) if profile \
        else boto3.session.Session(region_name=region)
    sm = sess.client("sagemaker", config=_BOTO_CFG)
    r = sm.describe_feature_group(FeatureGroupName=name)  # <-- 여기서 FeatureGroup 메타 획득

    # 핵심만 추림: Feature 정의(=컬럼), 스토어 위치
    feats: List[Dict[str, Any]] = []
    for f in r.get("FeatureDefinitions", []):
        feats.append({
            "name": f.get("FeatureName"),
            "type": f.get("FeatureType"),     # Integral/Float/String 등
            "description": None               # 필요 시 추가 메타 병합
        })

    offline = (r.get("OfflineStoreConfig") or {}).get("S3StorageConfig") or {}
    online  = r.get("OnlineStoreConfig") or {}
    record_id = r.get("RecordIdentifierFeatureName")
    event_ts  = r.get("EventTimeFeatureName")

    return {
        "featureGroupName": r.get("FeatureGroupName"),
        "featureGroupArn": r.get("FeatureGroupArn"),
        "recordIdentifier": record_id,
        "eventTime": event_ts,
        "features": feats,
        "offlineStore": {
            "s3_uri": offline.get("S3Uri"),
            "kms_key_id": offline.get("KmsKeyId"),
        },
        "onlineStore": {
            "enabled": online.get("EnableOnlineStore", False)
        },
        "creationTime": r.get("CreationTime").isoformat() if r.get("CreationTime") else None,
        "roleArn": r.get("RoleArn"),
        "status": r.get("FeatureGroupStatus"),
    }

def list_feature_groups(region: str, profile: Optional[str]=None, name_contains: Optional[str]=None) -> List[Dict[str, Any]]:
    sess = boto3.session.Session(profile_name=profile, region_name=region) if profile \
        else boto3.session.Session(region_name=region)
    sm = sess.client("sagemaker", config=_BOTO_CFG)

    out, token = [], None
    while True:
        kw = {"NextToken": token} if token else {}
        if name_contains:
            kw["NameContains"] = name_contains
        resp = sm.list_feature_groups(**kw)
        out.extend(resp.get("FeatureGroupSummaries", []))
        token = resp.get("NextToken")
        if not token: break

    return [{"name": i.get("FeatureGroupName"), "arn": i.get("FeatureGroupArn")} for i in out]
