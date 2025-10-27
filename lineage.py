import argparse, json, sys, re, datetime as dt
from typing import Dict, List, Any, Tuple, Optional
import boto3
import botocore

# ---------------------------
# Helpers (refs & S3 parsing)
# ---------------------------

_REF_RE = re.compile(r"Steps\.([A-Za-z0-9\-_]+)")
_S3_RE  = re.compile(r"^s3://([^/]+)/?(.*)$")

def _extract_ref_step(ref: dict | str) -> str | None:
    """{"Get":"Steps.Preprocess...."} -> "Preprocess" 같은 source step 이름 추출"""
    if isinstance(ref, dict):
        ref = ref.get("Get") or ref.get("Std:Ref") or ""
    if not isinstance(ref, str):
        return None
    m = _REF_RE.search(ref)
    return m.group(1) if m else None

def _s3_split(uri: str) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(uri, str): return None, None
    m = _S3_RE.match(uri)
    if not m: return None, None
    return m.group(1), m.group(2)  # bucket, key

def _iso(s) -> str:
    return s.isoformat() if hasattr(s, "isoformat") else (str(s) if s is not None else None)

# ---------------------------
# SageMaker list / pick
# ---------------------------

def list_domains(sm) -> List[Dict[str, Any]]:
    out, token = [], None
    while True:
        resp = sm.list_domains(NextToken=token) if token else sm.list_domains()
        out.extend(resp.get("Domains", []))
        token = resp.get("NextToken")
        if not token: break
    return out

def pick_domain_by_name(domains, name: str) -> Optional[Dict[str, Any]]:
    if not name: return None
    for d in domains:
        if d.get("DomainName") == name:
            return d
    return None

def list_all_pipelines(sm) -> List[Dict[str, Any]]:
    out, token = [], None
    while True:
        resp = sm.list_pipelines(NextToken=token) if token else sm.list_pipelines()
        out.extend(resp.get("PipelineSummaries", []))
        token = resp.get("NextToken")
        if not token: break
    return out

def pipeline_has_domain_tag(sm, arn: str, domain_id: Optional[str], domain_name: Optional[str]) -> bool:
    try:
        tags = sm.list_tags(ResourceArn=arn).get("Tags", [])
    except Exception:
        return False
    kv = {t["Key"]: t["Value"] for t in tags}
    if domain_id and kv.get("DomainId") == domain_id: return True
    if domain_name and kv.get("DomainName") == domain_name: return True
    return False

# ---------------------------
# Pipeline definition fetcher (robust)
# ---------------------------

def get_latest_execution_arn(sm, pipeline_name: str) -> str | None:
    ex = sm.list_pipeline_executions(
        PipelineName=pipeline_name,
        SortBy="CreationTime", SortOrder="Descending", MaxResults=1
    ).get("PipelineExecutionSummaries", [])
    return ex[0]["PipelineExecutionArn"] if ex else None

def get_pipeline_definition(sm, pipeline_name: str) -> dict:
    """
    1) get_pipeline (가능하면) → PipelineDefinition* 키 탐색
    2) 없으면 최신 실행으로 describe_pipeline_definition_for_execution
    """
    def _as_dict(v):
        if isinstance(v, dict): return v
        if isinstance(v, str):
            try: return json.loads(v)
            except Exception: return {}
        return {}

    # (A) get_pipeline: 일부 오래된 boto3엔 없을 수 있으니 try만
    try:
        r = sm.get_pipeline(PipelineName=pipeline_name)
        for k in ("PipelineDefinition", "PipelineDefinitionDocument", "PipelineDefinitionBody"):
            if k in r and r[k]:
                d = _as_dict(r[k])
                if d.get("Steps"): return d
                if isinstance(d.get("PipelineDefinition"), dict) and d["PipelineDefinition"].get("Steps"):
                    return d["PipelineDefinition"]
    except Exception as e:
        print(f"[warn] get_pipeline failed: {e}", file=sys.stderr)

    # (B) 최신 실행 정의
    try:
        arn = get_latest_execution_arn(sm, pipeline_name)
        if arn:
            r = sm.describe_pipeline_definition_for_execution(PipelineExecutionArn=arn)
            pd = r.get("PipelineDefinition") or r.get("PipelineDefinitionBody")
            d  = _as_dict(pd)
            if isinstance(d.get("PipelineDefinition"), dict) and d["PipelineDefinition"].get("Steps"):
                return d["PipelineDefinition"]
            return d
    except Exception as e:
        print(f"[warn] describe_pipeline_definition_for_execution failed: {e}", file=sys.stderr)

    return {}

# ---------------------------
# Step IO normalization
# ---------------------------

def normalize_step_io(step: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    stype = step.get("Type")
    args  = step.get("Arguments", {})
    inputs, outputs = [], []

    def add(uri: Optional[Any], io_type: str, name: str):
        # uri: str (s3) 또는 dict(ref) 모두 허용
        if uri is None: return
        (inputs if io_type == "input" else outputs).append({"name": name, "uri": uri})

    if stype == "Processing":
        for pin in args.get("ProcessingInputs", []):
            nm = pin.get("InputName") or "input"
            s3 = (pin.get("S3Input") or {}).get("S3Uri") or pin.get("DatasetDefinition") or pin.get("Input")
            add(s3, "input", nm)
        for pout in (args.get("ProcessingOutputConfig") or {}).get("Outputs", []):
            nm = pout.get("OutputName") or "output"
            s3 = (pout.get("S3Output") or {}).get("S3Uri")
            add(s3, "output", nm)

    elif stype == "Training":
        tjd = args.get("TrainingJobDefinition", {})
        for ch in tjd.get("InputDataConfig", []):
            nm = ch.get("ChannelName") or "channel"
            s3 = (((ch.get("DataSource") or {}).get("S3DataSource") or {}).get("S3Uri"))
            add(s3, "input", nm)
        s3out = (tjd.get("OutputDataConfig") or {}).get("S3OutputPath")
        add(s3out, "output", "model_artifacts")

    elif stype == "Transform":
        td = args.get("TransformJobDefinition", {})
        s3i = (((td.get("TransformInput") or {}).get("DataSource") or {}).get("S3DataSource") or {}).get("S3Uri")
        add(s3i, "input", "transform_input")
        s3o = (td.get("TransformOutput") or {}).get("S3OutputPath")
        add(s3o, "output", "transform_output")

    elif stype in ("ModelStep", "RegisterModel"):
        in_model = (args.get("Model").get("PrimaryContainer") or {}).get("ModelDataUrl") if args.get("Model") else None
        add(in_model, "input", "model_data")

    return inputs, outputs

# ---------------------------
# Build graph (nodes/edges/artifacts)
# ---------------------------

def build_graph_from_definition(pdef: Dict[str, Any]) -> Dict[str, Any]:
    steps = (
        pdef.get("Steps")
        or (pdef.get("PipelineDefinition") or {}).get("Steps")
        or (pdef.get("Definition") or {}).get("Steps")
        or []
    )

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    artifacts: List[Dict[str, Any]] = []
    artifact_index: Dict[str, int] = {}
    next_artifact_id = 0

    def index_artifact(uri: str) -> int:
        nonlocal next_artifact_id
        if uri not in artifact_index:
            artifact_index[uri] = next_artifact_id
            bucket, key = _s3_split(uri)
            artifacts.append({"id": next_artifact_id, "uri": uri, "bucket": bucket, "key": key})
            next_artifact_id += 1
        return artifact_index[uri]

    for s in steps:
        node_id = s["Name"]
        stype   = s.get("Type")
        depends = s.get("DependsOn", [])
        ins, outs = normalize_step_io(s)

        nodes.append({
            "id": node_id,
            "type": stype,
            "label": node_id,
            "inputs": ins,
            "outputs": outs
        })

        for prev in (depends if isinstance(depends, list) else [depends]):
            if prev:
                edges.append({"from": prev, "to": node_id, "via": "dependsOn"})

        for i in ins:
            u = i.get("uri")
            if isinstance(u, str) and u:
                index_artifact(u)
            elif isinstance(u, dict):
                src = _extract_ref_step(u)
                if src:
                    edges.append({"from": src, "to": node_id, "via": "ref:Get"})
        for o in outs:
            u = o.get("uri")
            if isinstance(u, str) and u:
                index_artifact(u)
            elif isinstance(u, dict):
                src = _extract_ref_step(u)
                if src:
                    edges.append({"from": src, "to": node_id, "via": "ref:Get"})

    return {"nodes": nodes, "edges": edges, "artifacts": artifacts}

# ---------------------------
# Build DATA-centric graph (bipartite: process <-> data)
# ---------------------------

def build_data_view_graph(graph_pipeline: Dict[str, Any]) -> Dict[str, Any]:
    """
    pipeline 그래프(graph_pipeline: nodes/edges/artifacts)를 이용해
    데이터-중심 이분 그래프를 생성한다.
    - process 노드: 기존 스텝
    - data 노드: 유니크한 아티팩트(URI)
    - edges: data->process(READ), process->data(WRITE)
    """
    nodes_p = graph_pipeline.get("nodes", [])
    artifacts = graph_pipeline.get("artifacts", [])

    # 1) data 노드 인덱스 (uri -> node)
    data_nodes: Dict[str, Dict[str, Any]] = {}
    def ensure_data_node(uri: str) -> Dict[str, Any]:
        uid = f"data:{uri.lower().rstrip('/')}"
        if uid not in data_nodes:
            # 해당 uri의 S3 메타 찾기(있으면 노드 data.meta.s3로)
            meta = {}
            for a in artifacts:
                if a.get("uri") == uri:
                    if a.get("s3"):
                        meta["s3"] = a["s3"]
                    meta["bucket"] = a.get("bucket")
                    meta["key"] = a.get("key")
                    break
            data_nodes[uid] = {
                "id": uid,
                "type": "dataArtifact",
                "label": uri,
                "uri": uri,
                "meta": meta
            }
        return data_nodes[uid]

    # 2) process 노드 구성(기존 스텝 재사용)
    proc_nodes: List[Dict[str, Any]] = []
    proc_index: Dict[str, Dict[str, Any]] = {}
    for n in nodes_p:
        pid = f"process:{n['id']}"
        item = {
            "id": pid,
            "type": "processNode",
            "label": n.get("label") or n["id"],
            "stepId": n["id"],
            "stepType": n.get("type"),
            "run": n.get("run"),          # 상태/소요시간/메트릭 재사용
            "registry": n.get("registry")
        }
        proc_nodes.append(item)
        proc_index[n["id"]] = item

    # 3) READ/WRITE 엣지 생성
    edges: List[Dict[str, Any]] = []

    def add_edge(src: str, dst: str, kind: str):
        eid = f"e:{src}->{dst}:{kind}"
        edges.append({"id": eid, "source": src, "target": dst, "kind": kind})

    for n in nodes_p:
        pid = f"process:{n['id']}"
        # READ: inputs (data -> process)
        for i in n.get("inputs", []):
            u = i.get("uri")
            if isinstance(u, str) and u:
                dnode = ensure_data_node(u)
                add_edge(dnode["id"], pid, "read")
        # WRITE: outputs (process -> data)
        for o in n.get("outputs", []):
            u = o.get("uri")
            if isinstance(u, str) and u:
                dnode = ensure_data_node(u)
                add_edge(pid, dnode["id"], "write")

    # 4) 결과
    data_nodes_list = list(data_nodes.values())
    return {
        "nodes": data_nodes_list + proc_nodes,
        "edges": edges
    }

# ---------------------------
# Enrich: latest execution (run info, metrics, registry)
# ---------------------------

def enrich_with_latest_execution(sm, pipeline_name: str, graph: Dict[str, Any]) -> None:
    try:
        ex_summ = sm.list_pipeline_executions(
            PipelineName=pipeline_name, SortBy="CreationTime", SortOrder="Descending", MaxResults=1
        ).get("PipelineExecutionSummaries", [])
        if not ex_summ: return
        exec_arn = ex_summ[0]["PipelineExecutionArn"]

        steps = sm.list_pipeline_execution_steps(PipelineExecutionArn=exec_arn)\
                  .get("PipelineExecutionSteps", [])

        node_map = {n["id"]: n for n in graph["nodes"]}

        for st in steps:
            name = st.get("StepName")
            node = node_map.get(name)
            if not node:
                continue

            node.setdefault("run", {})
            node["run"]["status"]    = st.get("StepStatus")
            node["run"]["startTime"] = _iso(st.get("StartTime", ""))
            node["run"]["endTime"]   = _iso(st.get("EndTime", ""))

            try:
                t1 = dt.datetime.fromisoformat(str(st.get("StartTime")).replace("Z","+00:00"))
                t2 = dt.datetime.fromisoformat(str(st.get("EndTime")).replace("Z","+00:00"))
                node["run"]["elapsedSec"] = max(0, int((t2 - t1).total_seconds()))
            except Exception:
                pass

            meta = st.get("Metadata") or {}

            # Processing
            pjob_arn = (meta.get("ProcessingJob") or {}).get("Arn")
            if pjob_arn:
                pj_name = pjob_arn.split("/")[-1]
                dj = sm.describe_processing_job(ProcessingJobName=pj_name)
                node["run"]["jobArn"]  = dj.get("ProcessingJobArn")
                node["run"]["jobName"] = dj.get("ProcessingJobName")
                inps, outs = [], []
                for p in dj.get("ProcessingInputs", []):
                    nm = p.get("InputName") or "input"
                    uri = (p.get("S3Input") or {}).get("S3Uri")
                    if uri: inps.append({"name": nm, "uri": uri})
                for o in (dj.get("ProcessingOutputConfig") or {}).get("Outputs", []):
                    nm = o.get("OutputName") or "output"
                    uri = (o.get("S3Output") or {}).get("S3Uri")
                    if uri: outs.append({"name": nm, "uri": uri})
                if inps: node["inputs"]  = inps
                if outs: node["outputs"] = outs

            # Training
            tjob_arn = (meta.get("TrainingJob") or {}).get("Arn")
            if tjob_arn:
                tj_name = tjob_arn.split("/")[-1]
                dj = sm.describe_training_job(TrainingJobName=tj_name)
                node["run"]["jobArn"]  = dj.get("TrainingJobArn")
                node["run"]["jobName"] = dj.get("TrainingJobName")

                metrics = {}
                for m in dj.get("FinalMetricDataList", []):
                    n = m.get("MetricName"); v = m.get("Value")
                    if n is not None and v is not None:
                        metrics[n] = v
                if metrics:
                    node["run"]["metrics"] = metrics

                inps, outs = [], []
                for ch in dj.get("InputDataConfig", []):
                    nm = ch.get("ChannelName") or "channel"
                    uri = (((ch.get("DataSource") or {}).get("S3DataSource") or {}).get("S3Uri"))
                    if uri: inps.append({"name": nm, "uri": uri})
                outp = (dj.get("ModelArtifacts") or {}).get("S3ModelArtifacts")
                if outp: outs.append({"name": "model_artifacts", "uri": outp})
                if inps: node["inputs"]  = inps
                if outs: node["outputs"] = outs

            # Model registry (RegisterModel step)
            model_pkg_arn = (meta.get("Model") or {}).get("ModelPackageArn") or \
                            (meta.get("RegisterModel") or {}).get("Arn")
            if model_pkg_arn:
                node.setdefault("registry", {})["modelPackageArn"] = model_pkg_arn

        # artifacts 재계산
        seen, artifacts = set(), []
        aid = 0
        for n in graph["nodes"]:
            for item in n.get("inputs", []) + n.get("outputs", []):
                u = item.get("uri")
                if isinstance(u, str) and u and u not in seen:
                    seen.add(u)
                    b, k = _s3_split(u)
                    artifacts.append({"id": aid, "uri": u, "bucket": b, "key": k})
                    aid += 1
        graph["artifacts"] = artifacts

    except Exception as e:
        print(f"[warn] enrich failed: {e}", file=sys.stderr)

# ---------------------------
# (1) Edges de-dup & label cleanup
# ---------------------------

def dedupe_and_label_edges(graph: Dict[str, Any]) -> None:
    seen = set()
    new_edges = []

    # to-node 기준 입력 이름 수집
    input_names: Dict[str, List[str]] = {}
    for n in graph["nodes"]:
        n_ins = [i.get("name") for i in n.get("inputs", []) if i.get("name")]
        if n_ins:
            input_names[n["id"]] = n_ins

    for e in graph["edges"]:
        key = (e["from"], e["to"], e.get("via"))
        if key in seen:
            continue
        seen.add(key)
        # 라벨 후보
        names = input_names.get(e["to"], [])
        names = [x for x in names if x != "code" and not str(x).startswith("input-")]
        if names:
            e["label"] = ", ".join(sorted(set(names))[:2])
        new_edges.append(e)

    graph["edges"] = new_edges

# ---------------------------
# (2) Pipeline summary
# ---------------------------

def pipeline_summary(graph: Dict[str, Any]) -> Dict[str, Any]:
    nodes = graph.get("nodes", [])
    status_counts: Dict[str, int] = {}
    starts, ends = [], []
    for n in nodes:
        st = ((n.get("run") or {}).get("status") or "Unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
        s = (n.get("run") or {}).get("startTime")
        e = (n.get("run") or {}).get("endTime")
        if s:
            try: starts.append(dt.datetime.fromisoformat(str(s).replace("Z","+00:00")))
            except Exception: pass
        if e:
            try: ends.append(dt.datetime.fromisoformat(str(e).replace("Z","+00:00")))
            except Exception: pass
    total = None
    if starts and ends:
        total = int((max(ends) - min(starts)).total_seconds())
    overall = ("Failed" if status_counts.get("Failed") else
               "Executing" if status_counts.get("Executing") else
               "Succeeded" if status_counts.get("Succeeded") else "Unknown")
    return {"overallStatus": overall, "nodeStatus": status_counts, "elapsedSec": total}

# ---------------------------
# (3) Evaluate report metrics from S3 (best-effort)
# ---------------------------

def enrich_eval_metrics_from_s3(session: boto3.session.Session, graph: Dict[str, Any]) -> None:
    s3 = session.client("s3")
    candidates = ("report.json", "evaluation.json", "metrics.json")
    for n in graph.get("nodes", []):
        if n.get("id") != "Evaluate":
            continue
        for o in n.get("outputs", []):
            if o.get("name") != "report" or not isinstance(o.get("uri"), str):
                continue
            b, k = _s3_split(o["uri"])
            if not b:
                continue
            try_keys = []
            if k.endswith(".json"):
                try_keys.append(k)
            for c in candidates:
                try_keys.append(k.rstrip("/") + "/" + c)
            for key in try_keys:
                try:
                    obj = s3.get_object(Bucket=b, Key=key)
                    data = json.loads(obj["Body"].read())
                    base = data.get("metrics") if isinstance(data, dict) else None
                    src = base if isinstance(base, dict) else data
                    if isinstance(src, dict):
                        vals = {f"eval.{kk}": vv for kk, vv in src.items() if isinstance(vv, (int, float))}
                        if vals:
                            n.setdefault("run", {}).setdefault("metrics", {}).update(vals)
                            n.setdefault("run", {})["reportObject"] = f"s3://{b}/{key}"
                            raise StopIteration
                except StopIteration:
                    break
                except botocore.exceptions.ClientError:
                    continue

# ---------------------------
# (4) S3 security metadata enrichment
# ---------------------------

def enrich_artifact_s3_meta(artifacts: List[Dict[str,Any]], session: boto3.session.Session):
    s3 = session.client("s3")
    for a in artifacts:
        b = a.get("bucket")
        if not b:
            continue
        meta = {"bucket": b}
        try:
            r = s3.get_bucket_location(Bucket=b)
            meta["region"] = r.get("LocationConstraint") or "us-east-1"
        except Exception:
            meta["region"] = "Unknown"
        try:
            r = s3.get_bucket_encryption(Bucket=b)
            rules = r["ServerSideEncryptionConfiguration"]["Rules"]
            meta["encryption"] = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        except Exception:
            meta["encryption"] = "Unknown"
        try:
            r = s3.get_bucket_versioning(Bucket=b)
            meta["versioning"] = r.get("Status","Disabled")
        except Exception:
            meta["versioning"] = "Unknown"
        try:
            r = s3.get_public_access_block(Bucket=b)
            cfg = r["PublicAccessBlockConfiguration"]
            meta["publicAccess"] = "Blocked" if all(cfg.values()) else "Partial"
        except Exception:
            meta["publicAccess"] = "Unknown"
        try:
            r = s3.get_bucket_tagging(Bucket=b)
            tags = {t["Key"]: t["Value"] for t in r.get("TagSet", [])}
            if tags: meta["tags"] = tags
        except Exception:
            pass
        a["s3"] = meta

# ---------------------------
# pipelines with domain (for /sagemaker/pipelines, /lineage/by-domain)
# ---------------------------

def list_pipelines_with_domain(region: str, profile: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    리전 내 파이프라인 + 태그를 함께 반환
    - matchedDomain: 파이프라인 태그에 DomainId/DomainName가 있으면 채워짐
    - tags: 비어 있으면 None(JSON에서는 null)
    """
    if profile:
        boto3.setup_default_session(profile_name=profile, region_name=region)
    sm = boto3.client("sagemaker", region_name=region)

    def _domain_id_from_arn(arn: str) -> Optional[str]:
        # arn:aws:sagemaker:ap-northeast-2:acct:domain/d-xxxxxxxxxxxx
        try:
            part = arn.split(":")[5]          # 'domain/d-xxxx'
            return part.split("/", 1)[1]      # 'd-xxxx'
        except Exception:
            return None

    pipes = list_all_pipelines(sm)

    # 미리 도메인 캐시
    doms = { d["DomainId"]: d for d in list_domains(sm) }
    doms_by_name = { d.get("DomainName"): d for d in doms.values() if d.get("DomainName") }

    out: List[Dict[str, Any]] = []
    for p in pipes:
        arn = p["PipelineArn"]
        try:
            tag_list = sm.list_tags(ResourceArn=arn).get("Tags", [])
        except Exception:
            tag_list = []
        kv = {t["Key"]: t["Value"] for t in tag_list}  # 원본 태그

        dom = None
        # 1) 명시적 태그 우선
        if "DomainId" in kv and kv["DomainId"] in doms:
            dom = doms[kv["DomainId"]]
        elif "DomainName" in kv and kv["DomainName"] in doms_by_name:
            dom = doms_by_name[kv["DomainName"]]
        else:
            # 2) 스튜디오 자동태그로 추론 (sagemaker:domain-arn)
            d_arn = kv.get("sagemaker:domain-arn")
            d_id = _domain_id_from_arn(d_arn) if d_arn else None
            if d_id and d_id in doms:
                dom = doms[d_id]

        out.append({
            "name": p["PipelineName"],
            "arn": arn,
            "lastModifiedTime": _iso(p.get("LastModifiedTime","")),
            "tags": (kv or None),  # 비어있으면 null
            "matchedDomain": (
                {"DomainId": dom["DomainId"], "DomainName": dom.get("DomainName")}
                if dom else None
            ),
        })
    return out

# =======================================================
# Entry used by API: build lineage graph for one pipeline
# =======================================================

def get_lineage_json(
    region: str,
    pipeline_name: str,
    domain_name: Optional[str] = None,
    include_latest_exec: bool = False,
    profile: Optional[str] = None,
    view: str = "both",
) -> Dict[str, Any]:
    """
    단건 파이프라인의 라인리지 그래프 데이터를 생성
    """
    # 세션 설정
    if profile:
        boto3.setup_default_session(profile_name=profile, region_name=region)
    sm = boto3.client("sagemaker", region_name=region)
    session = boto3.session.Session(profile_name=profile, region_name=region)

    # (1) 도메인 조회(선택)
    domains = list_domains(sm)
    selected = pick_domain_by_name(domains, domain_name) if domain_name else None
    domain_id = selected.get("DomainId") if selected else None

    # (2) 파이프라인 찾기 (+도메인 태그 필터)
    pipelines = list_all_pipelines(sm)
    target = None
    for p in pipelines:
        if p["PipelineName"] == pipeline_name:
            if not domain_id or pipeline_has_domain_tag(sm, p["PipelineArn"], domain_id, domain_name):
                target = p
                break
    if not target:
        raise ValueError(f"Pipeline '{pipeline_name}' not found or not tagged for the given domain.")
    
    # (2-1) 추가 확장 로직
    graph_pipeline = build_graph_from_definition(pdef)
    if include_latest_exec:
        enrich_with_latest_execution(sm, pipeline_name, graph_pipeline)
    dedupe_and_label_edges(graph_pipeline)
    try:
        enrich_eval_metrics_from_s3(session, graph_pipeline)
    except Exception:
        pass
    enrich_artifact_s3_meta(graph_pipeline["artifacts"], session)

    # (3) 정의 → 그래프 구성
    pdef = get_pipeline_definition(sm, pipeline_name)
    graph_pipeline = build_graph_from_definition(pdef)

    # (3-1) 최신 실행 보강
    if include_latest_exec:
        enrich_with_latest_execution(sm, pipeline_name, graph_pipeline)
    dedupe_and_label_edges(graph_pipeline)
    try:
        enrich_eval_metrics_from_s3(session, graph_pipeline)
    except Exception:
        pass
    enrich_artifact_s3_meta(graph_pipeline["artifacts"], session)

    # (3-5) 요약(파이프라인 기준)
    summary = pipeline_summary(graph_pipeline)

    # (3) 데이터-뷰 그래프 (필요 시)
    graph_data = build_data_view_graph(graph_pipeline) if view in ("data", "both") else None

    result = {
        "domain": (selected or {}),
        "pipeline": {
            "name": target["PipelineName"],
            "arn": target["PipelineArn"],
            "lastModifiedTime": _iso(target.get("LastModifiedTime", "")),
        },
        "summary": summary,
    }
    if view in ("pipeline", "both"):
        result["graphPipeline"] = graph_pipeline
        result["graph"] = graph_pipeline
    if view in ("data", "both"):
        result["graphData"] = graph_data

    return result

# ---------------------------
# Main (optional CLI)
# ---------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--pipeline-name", required=True)
    ap.add_argument("--domain-name", help="파이프라인에 달아둔 Tag(DomainName)로 필터링")
    ap.add_argument("--include-latest-exec", action="store_true", help="가장 최근 실행 정보를 반영해 입출력/지표 보강")
    ap.add_argument("--profile", help="AWS 프로필명(선택)")
    ap.add_argument("--out", help="JSON 파일로 저장 경로(선택)")
    args = ap.parse_args()

    data = get_lineage_json(
        region=args.region,
        pipeline_name=args.pipeline_name,
        domain_name=args.domain_name,
        include_latest_exec=args.include_latest_exec,
        profile=args.profile,
    )

    text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[ok] saved: {args.out}")
    else:
        print(text)

if __name__ == "__main__":
    main()
