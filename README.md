# DSPM Data Lineage Tracking API

AWS SageMaker 파이프라인의 데이터 라인리지를 자동 추출하고 시각화하는 FastAPI 서비스입니다.

## 주요 기능

- **SQL 기반 라인리지 추출**: CREATE TABLE, INSERT INTO 구문 자동 분석
- **SageMaker 파이프라인 분석**: Step별 입출력, 실행 상태, 메트릭 통합
- **스키마 버전 관리**: S3 데이터셋 스키마 자동 추출 및 버저닝
- **DAG 그래프 생성**: 프론트엔드 시각화를 위한 노드/엣지 구조 제공
- **Git 연동**: SQL 파일 자동 동기화 및 분석

## 빠른 시작

### 환경 구성
```bash
# 가상환경 설정
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

### 서버 실행
```bash
# 로컬 실행 (포트 8300)
uvicorn api:app --host 0.0.0.0 --port 8300 --reload
```

API 문서: http://localhost:8300/docs

## 프로젝트 구조
```
DSPM_DATA-Lineage-Tracking/
├── api.py                     # FastAPI 엔드포인트
├── lineage.py                 # 라인리지 생성/보강
├── modules/
│   ├── parquet_probe.py      # Parquet 스키마 추출
│   ├── schema_sampler.py     # JSON/CSV 샘플링
│   ├── schema_store.py       # 스키마 버저닝
│   ├── sql_lineage_light.py  # SQL 라인리지 추출
│   ├── sql_lineage_store.py  # SQL 파싱 결과 저장
│   └── connectors/
│       └── git_fetch.py      # Git 동기화
├── demo_repo/models/         # SQL 예시
├── dockerfile
└── requirements.txt
```

## API 엔드포인트

### 라인리지 조회
```bash
# 특정 파이프라인 라인리지
GET /lineage?pipeline={name}&region={region}&view=both&includeLatestExec=true

# 도메인별 라인리지
GET /lineage/by-domain?domain={name}&region={region}

# 파이프라인 목록
GET /sagemaker/pipelines?region={region}
```

### 스키마 관리
```bash
# 스키마 스캔 및 저장
POST /datasets/schema/scan?region={region}&s3_uri=s3://bucket/path

# 최신 스키마 조회
GET /datasets/{bucket}/{prefix}/schema

# 스키마 버전 목록
GET /datasets/{bucket}/{prefix}/schema/versions
```

### SQL 라인리지
```bash
# SQL 파싱
POST /sql/lineage
Content-Type: application/json
{"sql": "CREATE TABLE a AS SELECT x, y FROM b"}

# Inline SQL 체험
POST /tasks/sql/inline
Content-Type: application/json
{"pipeline": "demo", "sql": "CREATE TABLE..."}
```

### Health Check
```bash
GET /health
```

## 응답 예시
```json
{
  "summary": {
    "pipeline": "MyPipeline",
    "region": "ap-northeast-2",
    "steps": [
      {
        "id": "Preprocess",
        "type": "Processing",
        "inputs": [{"uri": "s3://bucket/in/data.csv"}],
        "outputs": [{"uri": "s3://bucket/out/processed.parquet"}],
        "run": {
          "status": "Succeeded",
          "elapsedSec": 245,
          "metrics": {"f1": 0.91}
        },
        "hasSql": true,
        "sqlDst": "db.output_table",
        "sqlSources": ["db.input_table"]
      }
    ]
  },
  "graphPipeline": {
    "nodes": [
      {"id": "process:Preprocess", "kind": "process", "label": "Preprocess"},
      {"id": "data:s3://bucket/in/data.csv", "kind": "data", "label": "data.csv"}
    ],
    "edges": [
      {"source": "data:s3://bucket/in/data.csv", "target": "process:Preprocess", "kind": "read"}
    ]
  }
}
```

## 필수 IAM 권한
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

## 데이터 처리 흐름
```
1. SQL 수집 (git_fetch.py / sql_collector.py)
   ↓
2. SQL 파싱 (sql_lineage_light.py)
   ↓
3. 라인리지 저장 (sql_lineage_store.py)
   ↓
4. SageMaker 메타데이터 결합 (lineage.py)
   ↓
5. 그래프 생성 (graphPipeline, graphData)
   ↓
6. FastAPI 응답 (api.py)
   ↓
7. Frontend DAG 시각화
```

## Frontend 연동

### API 호출 예시 (TypeScript)
```typescript
const response = await fetch(
  `/lineage?pipeline=${pipeline}&region=${region}&view=both&includeLatestExec=true`
);
const data = await response.json();

// Cytoscape.js 형식으로 변환
const elements = [
  ...data.graphPipeline.nodes.map(n => ({
    data: { id: n.id, label: n.label, kind: n.kind }
  })),
  ...data.graphPipeline.edges.map(e => ({
    data: { source: e.source, target: e.target, kind: e.kind }
  }))
];
```

### 시각화 라이브러리

- **Cytoscape.js** + elk layout: DAG 자동 정렬
- **vis-network**: 대화형 확대/축소

### UX 권장사항

- 노드 클릭: 사이드패널에서 상세 정보 표시
- 엣지 hover: read/write 방향 및 데이터 URI 표시
- 뷰 토글: Pipeline / Data / Both
- 상태별 색상: Succeeded(녹색), Failed(빨강), Executing(노랑)

## Docker 실행
```bash
# 이미지 빌드
docker build -t dspm-lineage .

# 컨테이너 실행
docker run -d \
  -p 8300:8300 \
  -v ~/.aws:/root/.aws:ro \
  -e AWS_DEFAULT_REGION=ap-northeast-2 \
  --name dspm-lineage \
  dspm-lineage

# 로그 확인
docker logs -f dspm-lineage
```

## 테스트 예시
```bash
# 1. 파이프라인 목록 조회
curl "http://localhost:8300/sagemaker/pipelines?region=ap-northeast-2"

# 2. 라인리지 조회
curl "http://localhost:8300/lineage?pipeline=MyPipeline&region=ap-northeast-2&view=both"

# 3. 스키마 스캔
curl -X POST "http://localhost:8300/datasets/schema/scan?region=ap-northeast-2&s3_uri=s3://my-bucket/data/"

# 4. SQL 파싱 체험
curl -X POST http://localhost:8300/tasks/sql/inline \
  -H "Content-Type: application/json" \
  -d '{"pipeline": "test", "sql": "CREATE TABLE output AS SELECT * FROM input"}'
```

## 트러블슈팅

### SageMaker 접근 오류
```bash
# IAM 권한 확인
aws sagemaker list-pipelines --region ap-northeast-2

# 자격 증명 확인
aws sts get-caller-identity
```

### S3 스키마 추출 실패
- 지원 포맷: JSON, CSV, Parquet
- 파일 크기: 샘플링은 처음 1000행만 사용
- 권한 확인: `s3:GetObject`, `s3:ListBucket`

### 포트 충돌
```bash
# 다른 포트 사용
uvicorn api:app --port 8301 --reload
```