import json, os, gzip, base64, urllib.request

MARQUEZ_URL = os.environ["MARQUEZ_URL"].rstrip("/")
PRODUCER = os.environ.get("PRODUCER", "urn:ai-dspm:cloudtrail:ingestor:1.0")
NS = os.environ.get("OL_NAMESPACE_PREFIX", "aws://")
AUTH_HEADER = os.environ.get("AUTH_HEADER")  # "Authorization: Bearer <token>" 형태 허용

def _post_ol(evt):
    data = json.dumps(evt).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if AUTH_HEADER:
        try:
            k, v = AUTH_HEADER.split(":", 1)
            headers[k.strip()] = v.strip()
        except Exception:
            headers["Authorization"] = AUTH_HEADER
    req = urllib.request.Request(f"{MARQUEZ_URL}/api/v1/lineage", data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read()

def _ct_to_ol(record):
    e = record
    acct = e.get("recipientAccountId", "unknown")
    region = e.get("awsRegion", "unknown")
    ns = f"{NS}{acct}/{region}"
    event_source = e.get("eventSource", "unknown")
    event_name = e.get("eventName", "UNKNOWN")
    job_name = f"{event_source.split('.')[0]}.{event_name}"
    run_id = e.get("eventID", "missing-id")

    inputs, outputs = [], []
    req = e.get("requestParameters") or {}
    res = e.get("responseElements") or {}

    # S3 대표 매핑 (1단계)
    if event_source == "s3.amazonaws.com":
        b = req.get("bucketName") or (req.get("bucket") or {}).get("name")
        k = req.get("key")
        if event_name in ("GetObject",):
            if b and k:
                inputs.append({"namespace": "s3", "name": f"{b}/{k}"})
        if event_name in ("PutObject", "CompleteMultipartUpload", "CopyObject"):
            if b and k:
                outputs.append({"namespace": "s3", "name": f"{b}/{k}"})

    return {
        "eventType": "START",
        "eventTime": e.get("eventTime"),
        "producer": PRODUCER,
        "schemaURL": "https://openlineage.io/spec/1-0-6/OpenLineage.json#/$defs/RunEvent",
        "run": {
            "runId": run_id,
            "facets": {
                "cloud": {"_producer": PRODUCER, "_schemaURL": "urn:custom", "accountId": acct, "region": region},
                "aws.agent": {
                    "userArn": (e.get("userIdentity") or {}).get("arn"),
                    "sourceIP": e.get("sourceIPAddress"),
                    "userType": (e.get("userIdentity") or {}).get("type")
                },
                "aws.cloudtrail": {"eventSource": event_source, "eventName": event_name, "eventID": run_id}
            }
        },
        "job": {"namespace": ns, "name": job_name},
        "inputs": [{"namespace": d["namespace"], "name": d["name"]} for d in inputs],
        "outputs": [{"namespace": d["namespace"], "name": d["name"]} for d in outputs]
    }

def lambda_handler(event, context):
    payload = event["awslogs"]["data"]
    data = gzip.decompress(base64.b64decode(payload))
    msg = json.loads(data)

    for wrapped in msg.get("logEvents", []):
        try:
            rec = json.loads(wrapped["message"])
            ol_evt = _ct_to_ol(rec)
            _post_ol(ol_evt)
        except Exception as ex:
            print("ERR", ex)
    return {"ok": True}
