import os, sys, time, hashlib, pathlib
sys.path.append("./lineage-emitter-py")
from lineage_emitter.emitter import start_run, complete_run

os.environ.setdefault("OL_NAMESPACE", "dspm.mlops")
os.environ.setdefault("MARQUEZ_ENDPOINT", "http://localhost:5000/api/v1/lineage")

def dataset_id_for_local(path: str) -> str:
    p = pathlib.Path(path).resolve()
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return f"file::{p}@sha256:{h.hexdigest()}"

if __name__ == "__main__":
    train_path = "./testdata/ml/train.csv" # 샘플 데이터 경로
    dataset_id = dataset_id_for_local(train_path)

    JOB = "train_model_min"   # 파이프라인 단계 이름
    run_id = start_run(
        JOB,
        inputs=[{"namespace":"file","name":dataset_id}],
        run_facets={}
    )
    # 실제로 여기서 모델 학습/전처리 등 작업이 수행됨
    time.sleep(1)

    # 산출물(모델 아티팩트)도 남기고 싶다면 name만 간단히 표기
    complete_run(
        run_id,
        JOB,
        outputs=[{"namespace":"ml-registry","name":"model:fraud/1.0.0"}],
        run_facets={}
    )
    print("OK: lineage event sent.")
