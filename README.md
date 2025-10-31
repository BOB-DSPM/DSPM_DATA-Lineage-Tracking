# SageMaker Lineage API — **v1.4.0**

**SageMaker 파이프라인의 데이터 라인리지(노드/엣지/아티팩트) + 실행상태 + 데이터스키마(Parquet/JSON/CSV) + Feature Store 메타**를
JSON으로 제공하는 경량 API입니다. HTTP 레이어는 `api.py`, 핵심 로직은 `lineage.py`이며, 데이터 스키마/증거 모듈은 `modules/*`에 있습니다.

> v1.3.0 문서를 기반으로 기능을 확장했습니다. (기존 문서 참고 사항은 그대로 유효)  
> **신규/변경 사항 요약**: `pyarrow` 기반 Parquet 스키마 추출, S3 경로 스키마 버저닝, SQL 가벼운 라인리지, SageMaker Feature Group 메타, 파이프라인 카탈로그 확장 등.

---

## ✨ 주요 기능 (v1.4.0 기준)

- SageMaker 파이프라인 **정의 → 그래프(nodes/edges/artifacts)** 구성
- 최신 실행(최근 1건) 기준 **상태/시간/메트릭/입출력/레지스트리** 보강 (`includeLatestExec=true`)
- **뷰 전환**: `view=pipeline | data | both` (파이프라인 의존 흐름 vs 데이터 중심 흐름)
- **데이터 스키마 수집**
  - **Parquet**: `pyarrow`로 **원격 S3에서 메타 스키마 추출** (정확)
  - **JSON/CSV**: Head 샘플링으로 타입 추정
  - **스키마 버저닝**: `modules/schema_store.py` — `dataset_id`, `policyHash`, `version` 기반 보관/조회
- **Feature Store**: SageMaker **Feature Group** 메타 조회/목록화
- **SQL 라이트 라인리지**: `INSERT..SELECT`/`CTAS`의 간단한 **src↔dst 컬럼 매핑** 추출
- **리전 카탈로그**: Region → Pipelines(+최신 실행 요약)
- **헬스체크**: `/health`

---

## 🧱 디렉터리 구조

```
.
├─ api.py                     # FastAPI 엔드포인트들
├─ lineage.py                 # 그래프 생성/보강, S3 메타, Evaluate 보고서 메트릭 수집
├─ modules/
│  ├─ parquet_probe.py        # pyarrow 기반 Parquet 스키마 추출
│  ├─ schema_sampler.py       # S3 JSON/CSV 샘플링 + Parquet 경로 위임
│  ├─ schema_store.py         # 스키마 버저닝 저장/조회(JSONL)
│  ├─ featurestore_schema.py  # SageMaker Feature Group 메타
│  └─ sql_lineage_light.py    # 단순 SQL 라인리지 추출
├─ requirements.txt           # fastapi, boto3, pyarrow 포함
├─ dockerfile                 # 컨테이너 실행(기본 포트 8300)
└─ README.md
```

---

## 🌐 API 엔드포인트

### 0) 상태
`GET /health` → `{ "status":"ok", "version":"1.4.0" }`

### 1) 파이프라인 카탈로그 (확장)
`GET /sagemaker/pipelines`
- `regions` (선택, 쉼표구분) — 예: `ap-northeast-2,us-east-1`
- `domainName` (선택) / `domainId` (선택) — 파이프라인 태그/매칭으로 필터
- `includeLatestExec` (선택, 기본 false) — 최신 실행 1건 요약 포함
- `profile` (선택, 개발용)

응답 예:
```json
{
  "regions": [
    {
      "region": "ap-northeast-2",
      "pipelines": [
        {
          "name": "mlops-pipeline",
          "arn": "arn:aws:sagemaker:...:pipeline/mlops-pipeline",
          "created": "2025-10-05T03:12:00Z",
          "tags": {"DomainName":"studio-a"},
          "matchedDomain": {"DomainName":"studio-a","DomainId":"d-xxxx"},
          "latestExecution": {"status":"Succeeded","arn":"...", "startTime":"...", "lastModifiedTime":"..."}
        }
      ]
    }
  ]
}
```

> 기존 `/sagemaker/overview`, `/sagemaker/catalog`도 계속 제공됩니다.

### 2) 라인리지 조회 (기존 + view 확장)
`GET /lineage`
- `region` (필수), `pipeline` (필수), `domain` (선택), `includeLatestExec` (선택)
- **`view` (선택)**: `pipeline | data | both` (기본 both)

`GET /lineage/by-domain`
- 도메인에 속한 모든 파이프라인 라인리지를 일괄 반환
- `region` (필수), `domain` (필수), `includeLatestExec` (선택)

### 3) 데이터 스키마 — 샘플 & 버전
- **샘플/저장**: `GET /datasets/{{bucket}}/{{prefix}}/schema?region=ap-northeast-2&save=true&policy={{json}}`
  - Parquet이면 `pyarrow`로 메타 추출, JSON/CSV는 샘플링
  - `save=true` + `policy`(任意 JSON) 시 `schema_store`에 버전 기록
- **버전 목록**: `GET /datasets/{{bucket}}/{{prefix}}/schema/versions?region=...`
  - 최신 순 정렬, `version`/`createdAt`/`policyHash`/`fields` 제공

응답 예(샘플):
```json
{
  "ok": true,
  "dataset_id": "s3://my-bucket/path/to/data/",
  "schema": {
    "format": "parquet",
    "fields": {"user_id":"int64","ts":"timestamp[us, tz=UTC]","score":"double"},
    "sampled_files": ["s3://my-bucket/path/to/data/part-0000.parquet"],
    "meta": {"num_row_groups": 4}
  },
  "saved": {"version": 3, "policyHash": "a1b2c3d4e5f6...."}  // save=true인 경우
}
```

### 4) SageMaker Feature Store
- **목록**: `GET /featurestore/feature-groups?region=ap-northeast-2`
- **상세**: `GET /featurestore/feature-groups/{{name}}?region=ap-northeast-2`
  - 반환: features(컬럼 정의/타입), offline/online store 설정, KMS, 생성시각, 상태 등

---

## ▶️ 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt

# 개발용 서버 실행(기본 8300)
uvicorn api:app --reload --port 8300
# 또는
python api.py
```

### 빠른 호출 예시
```bash
# 헬스체크
curl "http://localhost:8300/health"

# 파이프라인 카탈로그(리전 지정 + 최신 실행 포함)
curl "http://localhost:8300/sagemaker/pipelines?regions=ap-northeast-2&includeLatestExec=true"

# 단일 파이프라인 라인리지(데이터 중심 보기)
curl "http://localhost:8300/lineage?region=ap-northeast-2&pipeline=mlops-pipeline&view=data&includeLatestExec=true"

# 도메인 단위 일괄 라인리지
curl "http://localhost:8300/lineage/by-domain?region=ap-northeast-2&domain=studio-a&includeLatestExec=true"

# S3 경로 스키마 샘플링(+저장)
curl "http://localhost:8300/datasets/my-bucket/path/to/prefix/schema?region=ap-northeast-2&save=true"

# 스키마 버전 목록
curl "http://localhost:8300/datasets/my-bucket/path/to/prefix/schema/versions?region=ap-northeast-2"

# Feature Group 목록/상세
curl "http://localhost:8300/featurestore/feature-groups?region=ap-northeast-2"
curl "http://localhost:8300/featurestore/feature-groups/user_profiles?region=ap-northeast-2"
```

> 로컬 자격증명 사용: `AWS_PROFILE=dev` 환경변수 또는 쿼리스트링 `&profile=dev`  
> 운영 배포: 인스턴스 프로파일/IRSA 등 **Role 기반** 권장

---

## 🔐 최소 권한(IAM)

- SageMaker: `ListPipelines`, `GetPipeline`, `ListPipelineExecutions`, `DescribePipelineDefinitionForExecution`, `ListPipelineExecutionSteps`, `Describe*Job`, `ListTags`
- S3: `GetBucketLocation`, `GetBucketEncryption`, `GetBucketVersioning`, `GetPublicAccessBlock`, `GetBucketTagging`, `GetObject`(평가리포트/샘플용)
- (Feature Store 사용 시) `sagemaker:DescribeFeatureGroup`, `sagemaker:ListFeatureGroups`

리소스 범위는 **특정 파이프라인/버킷으로 제한**하는 것을 추천합니다.

---

## 🧑‍💻 프론트엔드 연동 가이드 (간단 ver.)

### 1) 데이터 소스 로딩
```ts
// 초기 1회: 카탈로그
const catalog = await fetch(`/sagemaker/pipelines?regions=ap-northeast-2&includeLatestExec=true`).then(r=>r.json());

// 사용자 선택에 따라
const region   = "ap-northeast-2";
const pipeline = "mlops-pipeline";
const domain   = "studio-a";

// 라인리지(뷰 전환 지원)
const lineage = await fetch(`/lineage?region=${region}&pipeline=${pipeline}&domain=${domain}&view=both&includeLatestExec=true`).then(r=>r.json());

// 데이터 스키마
const sch = await fetch(`/datasets/my-bucket/path/to/prefix/schema?region=${region}`).then(r=>r.json());

// Feature Group
const fgs = await fetch(`/featurestore/feature-groups?region=${region}`).then(r=>r.json());
```

### 2) 간단 UI 구성 제안
- **좌측 패널**: Region / Domain / Pipeline 선택 드롭다운 + 검색
- **상단 탭**: `Pipeline` | `Data` | `Both`
  - *Pipeline*: DAG(노드/엣지) + **스텝 상태 칩**(Succeeded/Failed/Executing) + 경과시간
  - *Data*: **Artifacts 리스트**(S3 URI) + 각 항목 클릭 시 **스키마 패널** 열기
  - *Both*: DAG와 아티팩트를 좌/우 Split로 동시 표시
- **우측 상세 패널** (선택 시 표시)
  - 노드: 입력/출력 URI, 실행시간, 상태, (있으면) **메트릭(JSON)** 미니 테이블
  - 아티팩트: S3 메타(Region/암호화/버저닝/PublicAccess), **스키마 필드/타입**, 샘플 파일 목록
- **부가**: Feature Group 탭(목록 → 선택 시 컬럼/스토어 설정 표시), 스키마 **버전 드롭다운**

> 그래프는 React Flow / Cytoscape.js, 테이블은 shadcn/ui + Tailwind 조합을 권장합니다.

---

## 🧩 코드 변화 포인트 (추가된 모듈/엔드포인트 설명)

- `modules/parquet_probe.py`
  - `pyarrow` + `S3FileSystem`으로 **원격 S3의 Parquet 메타 스키마**를 정확히 추출합니다.
- `modules/schema_sampler.py`
  - S3 프리픽스에서 **최대 N개 오브젝트**를 샘플링하고, 포맷별(JSON/CSV/Parquet) 스키마를 **머지**합니다.
- `modules/schema_store.py`
  - `dataset_id`(s3://bucket/prefix), `policy`(任意 JSON) → `policyHash` 기반 **버저닝 저장/조회**.
- `modules/sql_lineage_light.py`
  - `INSERT .. SELECT` / `CREATE TABLE AS SELECT` 구문에서 **src/dst/cols**를 가볍게 추출(실서비스는 `sqlglot` 권장).
- `modules/featurestore_schema.py`
  - Feature Group **목록/상세** API용 래퍼.
- `api.py` (핵심 라우트 추가)
  - `GET /sagemaker/pipelines`
  - `GET /datasets/{{bucket}}/{{prefix}}/schema`
  - `GET /datasets/{{bucket}}/{{prefix}}/schema/versions`
  - `GET /featurestore/feature-groups`, `GET /featurestore/feature-groups/{name}`
  - `GET /lineage`의 `view` 파라미터 지원

---

## 🐳 Docker 실행 예시

```bash
docker build -t lineage-api:1.4 .
docker run --rm -p 8300:8300 -e AWS_PROFILE=default -v ~/.aws:/root/.aws:ro lineage-api:1.4
# 헬스체크
curl http://localhost:8300/health
```

---

## ⚠️ 운영 팁

- **리전 제한**: 대규모 계정은 `ALLOWED_REGIONS`(환경변수)로 제한하고, 프론트는 **필터링만** 수행
- **캐시**: 파이프라인/카탈로그 응답은 프런트에서 30~60초 정도 캐싱
- **보안**: CORS/최소권한/IAM Role/CloudWatch Logs/헬스체크 설정 권장
- **PyArrow**: Lambda 컨테이너 등에서는 **플랫폼 빌드** 주의(이미지 기반 배포 권장)

---

## 📎 샘플 응답 스니펫 (라인리지 요약)

```json
{
  "pipeline": {"name":"mlops-pipeline","arn":"...","lastModifiedTime":"..."},
  "summary": {
    "overallStatus":"Succeeded",
    "nodeStatus":{"Succeeded":12,"Failed":0,"Executing":0},
    "elapsedSec": 1234
  },
  "graph": {
    "nodes":[{"id":"Preprocess","type":"Processing","inputs":[...],"outputs":[...],"run":{"status":"Succeeded"}}],
    "edges":[{"from":"Preprocess","to":"Train","via":"dependsOn"}],
    "artifacts":[{"id":0,"uri":"s3://bucket/path/part-0000.parquet","s3":{"region":"ap-northeast-2","encryption":"AES256"}}]
  }
}
```
