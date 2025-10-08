# SageMaker Lineage API (FastAPI + boto3)

**SageMaker 파이프라인의 데이터 라인리지(노드/엣지/아티팩트)와 실행 상태**를 JSON으로 제공하는 API로,  
핵심 로직은 `lineage.py`의 `get_lineage_json()`이며, HTTP 레이어는 `api.py`가 담당한다.

---

## ✨ 제공 기능

- SageMaker 파이프라인 **정의**를 읽어 **그래프**(nodes / edges / artifacts) 구성
- 최신 실행(최근 1건) 기준으로 **상태/시간/메트릭/입출력/레지스트리** 보강 (옵션)
- Evaluate 스텝 산출(`report.json`/`evaluation.json`/`metrics.json`)에서 **평가 지표** 추가 시도 (옵션)
- S3 버킷 **보안 메타** 수집: Region / 암호화 / 버저닝 / Public Access / 태그
- **헬스체크** 엔드포인트
- **카탈로그**: Region → Domain → Pipeline 구조로 조회
- **도메인 단위 일괄 라인리지** 및 **단일 파이프라인 라인리지** 조회
- **리전 개요**: 다수 리전을 한 번에 스캔해 `region → {domains[], pipelines[]}`로 반환

---

## 🧱 디렉터리 구조

```
.
├─ api.py            # FastAPI 엔드포인트 (/health, /sagemaker/overview, /sagemaker/catalog, /lineage, /lineage/by-domain)
├─ lineage.py        # boto3 로직 + get_lineage_json(), 인벤토리 유틸
└─ requirements.txt  # 필요한 파이썬 라이브러리 목록
```

---

## 🔌 전체 동작 흐름

1. 페이지 초기 로딩 시 `GET /sagemaker/overview?includeLatestExec=true` 호출 → **리전별 Domain + Pipeline**을 한 번에 수신
2. 프론트에서 지역/도메인/파이프라인을 **필터링만** 수행(재호출 없음)
3. 사용자가 특정 파이프라인을 선택하면 `GET /lineage` 호출로 상세 그래프/요약 조회
4. 필요 시 도메인 단위로 `GET /lineage/by-domain` 호출(해당 도메인의 모든 파이프라인 일괄)

> 기존 방식(`/sagemaker/catalog`)도 유지되며, 특정 리전만 빠르게 보고 싶을 때 유용함.

반환 스키마(요약):
```jsonc
{
  "domain": {...},          // 선택: DomainName 태그 필터 사용 시
  "pipeline": { "name": "...", "arn": "...", "lastModifiedTime": "..." },
  "summary": {
    "overallStatus": "Succeeded|Executing|Failed|Unknown",
    "nodeStatus": { "Succeeded": n, "Failed": m, ... },
    "elapsedSec": 1234
  },
  "graph": {
    "nodes": [ { "id": "...", "type": "...", "inputs": [...], "outputs": [...], "run": {...} } ],
    "edges": [ { "from": "...", "to": "...", "via": "dependsOn|ref:Get", "label": "..." } ],
    "artifacts": [ { "id": 0, "uri": "s3://.../...", "bucket": "...", "key": "...", "s3": {...} } ]
  }
}
```

---

## ▶️ 로컬 실행 (테스트용)

### 0) 요구 사항
- Python 3.10+
- AWS 자격증명(개발 시 `aws configure --profile <name>`)

### 1) 가상환경 (권장)
```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2) 의존성 설치
```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3) 서버 실행
```bash
# 모듈 실행(핫리로드)
python -m uvicorn api:app --reload --port 8000

# 또는 파일 실행
python api.py
```

### 4) 빠른 테스트
```bash
# 헬스체크
curl "http://localhost:8000/health"

# (예) 리전 개요: 다수 리전 스캔 + 최신 실행 포함
curl "http://localhost:8000/sagemaker/overview?includeLatestExec=true&regions=ap-northeast-2"

# (예) 카탈로그: 특정 리전만
curl "http://localhost:8000/sagemaker/catalog?regions=ap-northeast-2"

# (예) 도메인 단위 일괄 라인리지
curl "http://localhost:8000/lineage/by-domain?region=ap-northeast-2&domain=<DOMAIN_NAME>&includeLatestExec=true"

# (예) 단일 파이프라인 라인리지
curl "http://localhost:8000/lineage?region=ap-northeast-2&pipeline=<PIPELINE_NAME>&domain=<DOMAIN_NAME>&includeLatestExec=true"
```

> 개발 중 로컬 AWS 프로필을 사용하려면 쿼리스트링에 `&profile=default` 추가 또는 환경변수 `AWS_PROFILE=default`로 지정.  
> 운영 배포에서는 프로필 파라미터 제거 + **IAM Role** 사용 권장.

---

## 🌐 API 사양

### `GET /health`
상태 및 버전 확인.
```json
{ "status": "ok", "version": "1.3.0" }
```

### `GET /sagemaker/overview`
- 설명: 여러 리전을 한 번에 스캔하여 `region → {domains[], pipelines[]}` 구조 반환
- 쿼리:
  - `regions` (선택) — 쉼표구분 리전 목록. 미지정 시 SageMaker 지원 리전 전체 시도
  - `includeLatestExec` (선택, 기본 `false`) — 파이프라인별 최신 실행 1건 요약 포함
  - `profile` (선택, 개발용) — 로컬 AWS 프로필명(운영 미사용/무시 권장)
- 응답 예시:
```json
{
  "regions": [
    {
      "region": "ap-northeast-2",
      "domains": [
        {"domainId":"d-xxxx","domainArn":"...","domainName":"team-dev","status":"InService"}
      ],
      "pipelines": [
        {
          "pipelineName":"mlops-pipeline",
          "pipelineArn":"...",
          "created":"2025-10-05T03:12:00Z",
          "latestExecution": {
            "status":"Succeeded",
            "arn":"...",
            "startTime":"2025-10-05T03:13:00Z",
            "lastModifiedTime":"2025-10-05T03:25:10Z"
          }
        }
      ]
    }
  ]
}
```

### `GET /sagemaker/catalog`
리전별 도메인/파이프라인 카탈로그.
- 쿼리:  
  - `regions` (선택) — 쉼표구분 리전 목록. 미지정 시 SageMaker 지원 리전 전체 시도  
  - `profile` (선택, 개발용) — 로컬 AWS 프로필명

응답 예시:
```json
{
  "regions": [
    {
      "region": "ap-northeast-2",
      "domains": [{ "DomainId": "d-xxxxxx", "DomainName": "studio-a" }],
      "pipelines": [
        {
          "name": "mlops-pipe",
          "arn": "arn:aws:sagemaker:...:pipeline/mlops-pipe",
          "lastModifiedTime": "2025-10-08T12:34:56+00:00",
          "tags": { "DomainName": "studio-a" },
          "matchedDomain": { "DomainId": "d-xxxxxx", "DomainName": "studio-a" }
        }
      ]
    }
  ]
}
```

### `GET /lineage`
단일 파이프라인 라인리지 조회.
- 쿼리:  
  - `pipeline` (필수) — 파이프라인 이름  
  - `region` (필수) — 예: `ap-northeast-2`  
  - `domain` (선택) — DomainName 태그 필터  
  - `includeLatestExec` (선택) — `true`면 최신 실행 정보 보강  
  - `profile` (선택, 개발용) — 로컬 AWS 프로필명

### `GET /lineage/by-domain`
도메인에 매칭된 모든 파이프라인 라인리지를 일괄 반환.
- 쿼리:  
  - `region` (필수) — 리전  
  - `domain` (필수) — DomainName  
  - `includeLatestExec` (선택) — 최신 실행 포함 여부  
  - `profile` (선택, 개발용) — 로컬 프로필명

응답:
```jsonc
{
  "region": "ap-northeast-2",
  "domain": "studio-a",
  "count": 2,
  "results": [
    { "pipeline": "mlops-a", "ok": true,  "data": { /* lineage JSON */ } },
    { "pipeline": "mlops-b", "ok": false, "error": "권한/리소스 오류 등" }
  ]
}
```

---

## 🔐 최소 권한(IAM 예시)

조회 권한 위주로 구성하세요(버킷/리소스 ARN은 환경에 맞게 제한 권장).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker:ListPipelines",
        "sagemaker:GetPipeline",
        "sagemaker:ListPipelineExecutions",
        "sagemaker:DescribePipelineDefinitionForExecution",
        "sagemaker:ListPipelineExecutionSteps",
        "sagemaker:DescribeProcessingJob",
        "sagemaker:DescribeTrainingJob",
        "sagemaker:ListTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:GetBucketEncryption",
        "s3:GetBucketVersioning",
        "s3:GetPublicAccessBlock",
        "s3:GetBucketTagging"
      ],
      "Resource": "arn:aws:s3:::*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::<EVAL_REPORT_BUCKET>/*"
    }
  ]
}
```

---

## 🧑‍💻 프론트엔드 연동 예시

```js
// 1) 개요로 트리 로드(초기 1회)
const overview = await fetch("/sagemaker/overview?includeLatestExec=true").then(r => r.json());

// 2) 사용자가 Region/Domain/Pipeline 선택 (필터는 프론트에서만)
const region = "ap-northeast-2";
const pipeline = "mlops-pipe";

// 3) 단일 파이프라인 라인리지
const oneRes = await fetch(`/lineage?region=${region}&pipeline=${pipeline}&includeLatestExec=true`).then(r => r.json());

// 4) 도메인 전체 라인리지
const domain = "studio-a";
const allRes = await fetch(`/lineage/by-domain?region=${region}&domain=${domain}&includeLatestExec=true`).then(r => r.json());
```

> 브라우저 CORS 에러가 발생하면 `api.py`의 CORS 설정에서 `allow_origins`에 프론트 도메인을 명시할 수 있으며, 현재 템플릿은 `*`로 열려 있음.

---

## 🐳 Docker (선택)

**Dockerfile 예시**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY api.py lineage.py requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

빌드/실행:
```bash
docker build -t lineage-api .
docker run --rm -p 8000:8000 lineage-api
# (로컬 자격증명 사용 시) -v ~/.aws:/root/.aws:ro -e AWS_PROFILE=default 추가
```

---

## ⚠️ 주의 & 팁

- 대규모 계정/리전에서 전 리전 스캔은 느릴 수 있으므로 운영에서는 `ALLOWED_REGIONS`로 제한하고, `/sagemaker/overview` 캐시(`OVERVIEW_TTL_SECONDS`)를 권장함.
- Evaluate 리포트 탐색 규칙은 `Evaluate` 스텝의 `report` 출력 경로에서 파일명을 **우선 탐색**하므로, 명명 규칙이 다르면 코드에서 조건을 맞춰야 함.
- 프로덕션에서는 CORS 제한, 최소 권한, 모니터링/로깅, 헬스체크(현재 `/health`) 설정을 권장함.