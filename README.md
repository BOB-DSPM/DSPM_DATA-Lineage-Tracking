# SageMaker MLOps Lineage API (CDK + Lambda + API Gateway)

이 저장소는 **AWS SageMaker Pipeline**의 **데이터 흐름(Lineage)** 과 **보안 메타데이터**를 추출하여
DSPM 대시보드에서 사용할 수 있는 **JSON**으로 반환하는 **Lineage API** 기능 구현에 대한 내용입니다.

백엔드는 **AWS CDK (TypeScript)**, **Amazon API Gateway**, **AWS Lambda (Python)** 로 구성됩니다.
Lambda는 로컬에서 검증한 `lineage_dump.py`(boto3 로직)를 그대로 사용하여
**노드/엣지/아티팩트** 그래프를 만들고, **최신 실행 정보/지표/모델 레지스트리**와 **S3 보안 메타데이터**(암호화/버저닝/퍼블릭 접근/태그)를 보강합니다.

---

## ✨ 특징

- SageMaker **파이프라인 정의** + **최신 실행** 조회
- **그래프 JSON** 제공: `nodes`, `edges`, `artifacts`
- 스텝별 **실행 정보**: 상태, 시작/종료, 소요시간, Job ARN, 학습 지표 등
- **S3 보안 메타데이터**: 암호화 방식, 버저닝, Public Access, 태그
- 파이프라인 **요약**: 전체 상태/스텝별 카운트/총 소요시간
- **REST API** + **CORS 허용** → 프론트에서 직접 호출 가능
- **CDK 한 번의 명령**으로 배포

---

## 🧭 전체 흐름

```text
Frontend (React/Next)
    │   GET /prod/lineage?pipeline=mlops-pipeline&includeLatestExec=true
    ▼
API Gateway (REST, CORS)
    ▼
Lambda (Python)  ── 호출 ──► SageMaker / S3 (읽기 전용)
    │   - 파이프라인 정의/최신 실행 파싱
    │   - 노드/엣지/아티팩트 구성
    │   - 실행 지표 + S3 보안 메타데이터 보강
    ▼
JSON 응답
    { pipeline, summary, graph: { nodes, edges, artifacts } }
```

---

## 📦 디렉터리 구조

```
.
├── cdk.json
├── package.json
├── bin/
│   └── lineage-api.ts               # CDK 앱 진입점
├── lib/
│   └── lineage-api-stack.ts         # API Gateway + Lambda + IAM 정의
├── lambda/
│   ├── handler.py                   # Lambda 핸들러(내부에서 lineage_dump를 호출)
│   └── lineage_dump.py              # boto3 로직(로컬에서 검증한 최종본)
└── README.md
```

> `lineage_dump.py`는 원래 CLI로 JSON을 출력하지만, Lambda에서는 모듈로 임포트되어 **함수 형태로** 실행되어 API 응답 본문으로 반환합니다.

---

## 🔐 IAM 최소 권한

Lambda 실행 역할에 아래 **읽기 전용** 권한이 필요합니다.

- **SageMaker**: `ListPipelines`, `GetPipeline`(지원 시),  
  `DescribePipelineDefinitionForExecution`, `ListPipelineExecutions`,
  `ListPipelineExecutionSteps`, `DescribeProcessingJob`, `DescribeTrainingJob`
- **S3 (버킷 수준)**: `GetBucketLocation`, `GetBucketEncryption`,
  `GetBucketVersioning`, `GetPublicAccessBlock`, `GetBucketTagging`
- **S3 (옵션, 개체 읽기)**: `GetObject` (평가 리포트 JSON을 읽을 때)

> CDK에서 위 권한을 부여합니다. 가능하면 S3 리소스는 조직 버킷으로 **스코프 제한**하세요
> (예: `arn:aws:s3:::my-mlops-dev2-v2-main-data` 및 필요한 prefix).

---

## ⚙️ API 명세

**Base URL** (배포 후 CDK 출력 참고):

```
https://<apiId>.execute-api.<region>.amazonaws.com/prod
```

**엔드포인트**

```
GET /lineage
```

**쿼리 파라미터**

| 이름                | 타입    | 필수 | 기본값            | 설명 |
|---------------------|---------|------|-------------------|------|
| `pipeline`          | string  | ✅   | -                 | SageMaker 파이프라인 이름 |
| `region`            | string  | ❌   | `ap-northeast-2`  | 파이프라인이 있는 리전 |
| `includeLatestExec` | boolean | ❌   | `true`            | 최신 실행(상태/지표/IO) 보강 |
| `domain`            | string  | ❌   | -                 | (사용 시) 도메인 이름 태그로 필터 |

**성공 (200) 응답 예시**

```jsonc
{
  "pipeline": { "name": "...", "arn": "...", "lastModifiedTime": "..." },
  "summary": { "overallStatus": "Succeeded", "nodeStatus": {"Succeeded": 6}, "elapsedSec": 783 },
  "graph": {
    "nodes": [
      {
        "id": "Train",
        "type": "Training",
        "inputs": [{ "name": "train", "uri": "s3://..." }],
        "outputs": [{ "name": "model_artifacts", "uri": "s3://.../model.tar.gz" }],
        "run": {
          "status": "Succeeded",
          "elapsedSec": 170,
          "jobArn": "arn:aws:sagemaker:...:training-job/...",
          "metrics": { "validation:auc": 0.63, "train:auc": 0.72 }
        }
      }
    ],
    "edges": [{ "from": "Preprocess", "to": "Train", "via": "ref:Get" }],
    "artifacts": [
      {
        "id": 8,
        "uri": "s3://.../model.tar.gz",
        "bucket": "my-mlops-dev2-v2-main-data",
        "key": "pipelines/.../model.tar.gz",
        "s3": { "encryption": "aws:kms", "versioning": "Enabled", "publicAccess": "Blocked", "tags": { "Env": "development" } }
      }
    ]
  }
}
```

**오류**

- `400`: `{ "message": "pipeline is required" }`
- `404`: `{ "message": "pipeline not found or domain filter mismatched" }`
- `500`: `{ "message": "internal error", "requestId": "..." }` (CloudWatch Logs 확인)

---

## 🚀 배포

### 준비물
- Node.js 18+ / npm 또는 pnpm
- AWS CDK v2 (`npm i -g aws-cdk`)
- AWS CLI 프로파일 설정 (`aws configure`)
- Lambda 런타임용 Python 3.11

### 절차
```bash
# 1) 의존성 설치
npm install

# 2) (계정/리전 최초 1회) CDK 부트스트랩
cdk bootstrap aws://<ACCOUNT_ID>/<REGION>

# 3) 스택 배포
cdk deploy LineageApiStack
```

배포가 완료되면 출력 예:

```
LineageApiStack.LineageApiEndpoint = https://<apiId>.execute-api.<region>.amazonaws.com/prod
```

---

## 🧪 테스트

**macOS/Linux (curl)**

```bash
curl -s "https://<apiId>.execute-api.ap-northeast-2.amazonaws.com/prod/lineage?pipeline=mlops-pipeline&includeLatestExec=true&region=ap-northeast-2" \
  | python -m json.tool | head -n 50
```

**Windows PowerShell**

```powershell
$u = "https://<apiId>.execute-api.ap-northeast-2.amazonaws.com/prod/lineage?pipeline=mlops-pipeline&includeLatestExec=true&region=ap-northeast-2"
Invoke-RestMethod -Uri $u -Method GET | ConvertTo-Json -Depth 10
```

**로그 확인 (CloudWatch)**

```bash
aws logs tail /aws/lambda/LineageApiFn --follow --region ap-northeast-2
```

---

## 🖥️ 프론트엔드 연동

가장 간단한 fetch 예시:

```js
const url = `${API}/lineage?pipeline=mlops-pipeline&includeLatestExec=true&region=ap-northeast-2`;
const data = await fetch(url).then(r => {
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
});

// data.graph.nodes / edges / artifacts 로 그래프 렌더링
// 예: React Flow, Cytoscape, Dagre 등 사용
```

### 렌더링 팁
- 노드 색: `run.status`(Succeeded/Executing/Failed)에 따라 구분
- 엣지 스타일: `via` 값(`dependsOn` vs `ref:Get`)을 시각적으로 차별
- 우측 패널: 스텝 `run` 정보, 지표, 레지스트리, 아티팩트 `s3` 메타데이터 표시
- “Last scanned”는 노드 `run.endTime` 최대값 혹은 `pipeline.lastModifiedTime` 활용

---

## 🔧 설정

Lambda 환경변수(선택):

- `DEFAULT_REGION`: 기본 리전 오버라이드
- `ENABLE_S3_META`: `true|false` (S3 메타데이터 보강 토글)
- `S3_TIMEOUT_MS`: S3 클라이언트 타임아웃(선택)

> CDK 예: `fn.addEnvironment('ENABLE_S3_META','true')`

---

## 🔒 보안 / 최소 권한

- S3 권한은 **알려진 버킷/프리픽스**로 범위를 제한하세요.
- (필요 시) VPC 연결 시 NAT/프라이빗 엔드포인트 구성 필수 (AWS API 호출 가능해야 함).
- 쿼리 파라미터 유효성 점검(필수 파라미터 검증 포함).
- API Gateway 레이트 리밋/캐싱 고려.

---

## 🛠️ 트러블슈팅

- `encryption`/`publicAccess` 가 `Unknown` → Lambda 역할에 `GetBucketEncryption` 또는 `GetPublicAccessBlock` 권한이 부족하거나, 해당 리소스가 미설정일 수 있습니다.
- `500 internal error` → CloudWatch 로그에서 스택 트레이스 확인.
- `metrics` 비어있음 → 학습 작업이 `FinalMetricDataList`를 출력하지 않았거나 메트릭명 상이.
- 간선 없음 → 정의에 `DependsOn`/`Get` 참조가 없을 수 있음(최신 실행 보강으로 대부분 보완).

---

## 🧰 로컬 검증 (선택)

로컬에서 boto3로 JSON 검증이 필요하면:

```bash
python lineage_dump.py \
  --region ap-northeast-2 \
  --pipeline-name mlops-pipeline \
  --include-latest-exec \
  --out mlops-pipeline.json
```
