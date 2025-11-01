# SageMaker Lineage API — **v1.5.0**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)](https://fastapi.tiangolo.com/)
[![AWS](https://img.shields.io/badge/AWS-SageMaker-orange)](https://aws.amazon.com/sagemaker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 📘 프로젝트 개요

**DSPM_DATA-Lineage-Tracking**은 AWS **SageMaker 파이프라인의 데이터 라인리지(Data Lineage)** 를 자동 추출하고,  
각 Step의 **실행 상태, 입출력, S3 스키마, Feature Store 메타데이터, SQL 매핑** 등을 통합 관리하는 경량형 API 서버입니다.

FastAPI를 기반으로 구현되었으며, MLOps 파이프라인의 데이터 흐름을 **시각적으로 추적**하고 **데이터 거버넌스**를 강화하기 위해 설계되었습니다.

---

## ⚙️ 주요 기능

| 기능 구분 | 설명 |
|------------|------|
| **SQL 기반 라인리지 추출** | `CREATE TABLE AS SELECT`, `INSERT INTO SELECT` 구문 분석하여 Input→Output 테이블 및 컬럼 매핑 자동화 |
| **AWS SageMaker 파이프라인 분석** | 각 Step의 입력/출력, 최신 실행(Job) 메타데이터, 지표, 레지스트리 정보를 통합 라인리지 그래프로 구성 |
| **데이터 라인리지 시각화용 그래프 변환** | `graphPipeline`, `graphData` 노드·엣지 구조 생성 (DAG 기반) |
| **데이터셋 스키마 버전 관리** | `schema_store.py`를 통해 버전별 스키마 및 정책 관리(JSONL append 방식) |
| **S3 데이터 스키마 자동 샘플링** | JSON/CSV/Parquet 포맷 자동 감지 및 `pyarrow`, `boto3` 기반 스키마 추출 |
| **Git 연동 지원** | Git 저장소 내 SQL 자동 pull/fetch 후 최신 버전 기반 분석 가능 |
| **FastAPI 기반 REST 서비스화** | `/lineage`, `/datasets/schema`, `/sql/lineage` 등 REST API 제공 |
| **Inline SQL 체험 지원** | `/tasks/sql/inline` 엔드포인트를 통해 SQL 직접 입력·저장 후 라인리지 생성 |

---

## 🧱 디렉터리 구조

```
DSPM_DATA-Lineage-Tracking/
├─ api.py                     # FastAPI 엔드포인트
├─ lineage.py                 # SageMaker 파이프라인 라인리지 생성/보강
├─ modules/
│  ├─ parquet_probe.py        # pyarrow 기반 Parquet 스키마 추출
│  ├─ schema_sampler.py       # JSON/CSV 샘플링 + Parquet 위임
│  ├─ schema_store.py         # 스키마 버저닝 저장/조회(JSONL)
│  ├─ featurestore_schema.py  # Feature Store 메타데이터
│  ├─ sql_lineage_light.py    # SQL 라이트 라인리지 추출
│  ├─ sql_lineage_store.py    # SQL 파싱 결과 저장
│  ├─ sql_collector.py        # SQL 수집 모듈
│  └─ connectors/git_fetch.py # Git 기반 SQL 동기화
├─ demo_repo/models/          # 테스트용 SQL 예시 파일
├─ dockerfile                 # 컨테이너 빌드 파일
├─ requirements.txt           # 종속성 목록
└─ README.md
```

---

## 🚀 Local Execution (로컬 실행 방법)

### 1️⃣ 환경 구성
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2️⃣ 로컬 서버 실행
```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8300
```

브라우저에서 `http://localhost:8300/docs` 접속 → Swagger UI에서 테스트 가능.

### 3️⃣ 테스트 요청 예시
```bash
# 파이프라인 라인리지 조회
curl "http://localhost:8300/lineage?pipeline=MyPipe&region=ap-northeast-2&includeLatestExec=true&view=both"

# 데이터셋 스키마 스캔
curl -X POST "http://localhost:8300/datasets/schema/scan?region=ap-northeast-2&s3_uri=s3://my-bucket/data"

# Inline SQL 파싱 저장
curl -X POST -H "Content-Type: application/json"   -d '{"pipeline": "demo", "sql": "CREATE TABLE a AS SELECT x,y FROM b;"}'   http://localhost:8300/tasks/sql/inline
```

---

## 🌐 주요 API 엔드포인트

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | 서버 상태 확인 |
| GET | `/sagemaker/pipelines` | SageMaker 파이프라인 목록 조회 및 도메인 매핑 |
| GET | `/lineage` | 특정 파이프라인의 라인리지(그래프 포함) |
| GET | `/lineage/by-domain` | 도메인 내 모든 파이프라인 라인리지 조회 |
| POST | `/datasets/schema/scan` | S3 샘플 데이터를 기반으로 스키마 추출 및 저장 |
| GET | `/datasets/{bucket}/{prefix}/schema` | 데이터셋 최신/특정 버전 스키마 조회 |
| GET | `/datasets/{bucket}/{prefix}/schema/versions` | 스키마 버전 목록 조회 |
| POST | `/sql/lineage` | SQL 구문 파싱(Lineage 추출) |
| POST | `/tasks/sql/inline` | SQL 직접 입력/파싱 후 저장 (체험용) |

---

## 🔒 최소 IAM 권한

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker:ListPipelines",
        "sagemaker:DescribePipeline",
        "sagemaker:ListPipelineExecutionSteps",
        "sagemaker:ListPipelineExecutions",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## 🔁 데이터 처리 흐름

```
graph TD
A[SQL 수집 (Collector)] --> B[SQL 파싱 (sql_lineage_light)]
B --> C[라인리지 저장 (sql_lineage_store)]
C --> D[라인리지 생성 (lineage.py)]
D --> E[FastAPI 응답 (api.py)]
E --> F[Frontend DAG 시각화]
```

---

## 🧩 응답 예시

```json
{
  "summary": {
    "pipeline": "MyPipe",
    "region": "ap-northeast-2",
    "steps": [
      {
        "id": "Preprocess",
        "type": "Processing",
        "inputs": [{"uri": "s3://bucket/in/train.csv"}],
        "outputs": [{"uri": "s3://bucket/out/prep.parquet"}],
        "run": {"status": "Succeeded", "elapsedSec": 245, "metrics": {"eval.f1": 0.91}},
        "hasSql": true,
        "sqlDst": "db.tbl_out",
        "sqlSources": ["db.tbl_in"]
      }
    ]
  },
  "graphPipeline": {
    "nodes": [
      {"id": "process:Preprocess", "kind": "process", "label": "Preprocess"},
      {"id": "data:s3://bucket/in/train.csv", "kind": "data", "label": "train.csv"}
    ],
    "edges": [
      {"source": "data:s3://bucket/in/train.csv", "target": "process:Preprocess", "kind": "read"},
      {"source": "process:Preprocess", "target": "data:s3://bucket/out/prep.parquet", "kind": "write"}
    ]
  }
}
```

---

## 💻 Frontend 연동 가이드

### ✅ 호출 예시 (React/TypeScript)
```tsx
const res = await fetch(`/lineage?pipeline=${pipeline}&region=${region}&view=both&includeLatestExec=true`);
const data = await res.json();
const graph = data.graphPipeline;

const elements = [
  ...graph.nodes.map(n => ({ data: { id: n.id, label: n.label, kind: n.kind } })),
  ...graph.edges.map(e => ({ data: { source: e.source, target: e.target, kind: e.kind } }))
];
```

### 🎨 시각화 권장 라이브러리
- **Cytoscape.js** + **elk layout** → DAG 구조 정렬
- **vis-network** → 대화형 확대/축소 지원

### 💡 UX 권장 포인트
- 노드 클릭 → 사이드패널에 상세정보(SQL, Run, Metrics)
- 엣지 hover → `read` / `write` 방향 표시 및 데이터 URI 툴팁
- `Pipeline / Data / Both` 토글 지원
- 상태별 색상 구분 (`Succeeded`, `Failed`, `Executing` 등)

---

## 🧠 Inline SQL 체험 플로우

1️⃣ 사용자 SQL 입력 → POST `/tasks/sql/inline` 전송  
2️⃣ SQL 파싱 및 결과 저장 → `data/sql_lineage.jsonl`  
3️⃣ `/lineage?pipeline={pipeline}` 호출 시 Inline 스텝 포함 그래프 생성  

---

## 🐳 Docker 실행 (선택)

```bash
docker build -t dspm-lineage .
docker run -d -p 8300:8300 dspm-lineage
```
