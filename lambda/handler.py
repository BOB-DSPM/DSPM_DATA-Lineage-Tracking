# lambda/handler.py
import json, os
from lineage_lib import get_lineage_json

DEFAULT_REGION = os.environ.get("DEFAULT_REGION", "ap-northeast-2")

def _resp(status, body, headers=None):
    h = {"Content-Type":"application/json","Access-Control-Allow-Origin":"*","Access-Control-Allow-Headers":"*"}
    if headers: h.update(headers)
    return {"statusCode": status, "headers": h, "body": json.dumps(body, ensure_ascii=False)}

def handler(event, context):
    try:
        qs = event.get("queryStringParameters") or {}
        region = qs.get("region") or DEFAULT_REGION
        pipeline = qs.get("pipeline") or qs.get("pipelineName")
        domain   = qs.get("domain") or qs.get("domainName")
        include  = (qs.get("includeLatestExec","true").lower() != "false")
        if not pipeline:
            return _resp(400, {"message":"Missing 'pipeline' query parameter"})
        data = get_lineage_json(region=region, pipeline_name=pipeline, domain_name=domain, include_latest_exec=include)
        return _resp(200, data)
    except ValueError as ve:
        return _resp(404, {"message": str(ve)})
    except Exception as e:
        return _resp(500, {"message": f"Internal error: {e}"})
