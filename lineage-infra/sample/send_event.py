import sys, os, time, hashlib, pathlib
sys.path.append("/app/emitter")
from lineage_emitter.emitter import start_run, complete_run

def dataset_id_for_local(path: str) -> str:
    p = pathlib.Path(path).resolve()
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return f"file::{p}@sha256:{h.hexdigest()}"

train_path = "/data/test/ml/train.csv"
dataset_id = dataset_id_for_local(train_path)

rid = start_run("train_model",
                run_facets={"sagemaker":{"jobId":"train-local","algorithm":"xgboost"}},
                inputs=[{"namespace":"file","name":dataset_id}])
time.sleep(1)
complete_run(rid,"train_model",
             outputs=[{"namespace":"ml-registry","name":"model:fraud/1.2.0"}],
             run_facets={"modelRegistry":{"name":"fraud","version":"1.2.0","status":"Approved"}})
print("Sample local event sent.")
