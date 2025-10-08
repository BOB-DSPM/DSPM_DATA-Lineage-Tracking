# SageMaker Lineage API (FastAPI + boto3)

간단한 FastAPI 서버로 **SageMaker 파이프라인의 데이터 라인리지(노드/엣지/아티팩트)와 실행 상태**를 JSON으로 제공합니다.  
핵심 로직은 `lineage.py`의 `get_lineage_json()`에 있으며, HTTP 레이어는 `api.py`가 담당합니다.


## ✨ 기능 개요

- SageMaker 파이프라인 **정의**를 읽어 **그래프**(nodes/edges/artifacts) 구성
- 최신 실행 1건 기준으로 **상태/시간/메트릭/입출력/레지스트리** 등 정보 보강(옵션)
- Evaluate 스텝의 보고서 JSON(`report.json`, `evaluation.json`, `metrics.json`)에서 **평가 지표** 추가 시도(옵션)
- 각 S3 버킷의 **리전/암호화/버저닝/Public Access/태그** 등 **보안 메타데이터** 수집
- 최종 **요약**(overallStatus, nodeStatus 카운트, 총 소요시간) 제공

---

## 🧱 디렉터리 구조

```
.
├─ api.py           # FastAPI 엔드포인트 (/lineage)
├─ lineage.py       # boto3 로직 + get_lineage_json()
└─ requirements.txt # 필요한 파이썬 라이브러리 목록
```

---

## 🔌 전체 흐름(요청~응답)

1. 클라이언트가 `GET /lineage` 호출 (쿼리: `pipeline`, `region`, `domain?`, `includeLatestExec?`, `profile?`)
2. `api.py`가 `lineage.get_lineage_json()` 호출
3. `get_lineage_json()`은 SageMaker API로 파이프라인 정의를 가져와 **그래프** 구성
4. `includeLatestExec=true`면 최신 실행을 조회하여 각 스텝에 **run 정보**(status, start/end, metrics, IO, model registry 등) 보강
5. 필요 시 Evaluate 스텝의 결과 리포트를 S3에서 읽어 **평가 지표** 추가
6. **S3 보안 메타**를 수집하여 `graph.artifacts[*].s3`에 정리
7. **요약(summary)** 계산 후 아래 구조의 JSON으로 반환

반환 스키마(요약):
```jsonc
{
  "domain": {...},          // 선택: DomainName 태그로 필터링했을 때
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

## ▶️ 빠른 시작(로컬)

### 0) 요구 사항
- Python 3.10+
- AWS 자격증명(로컬 개발 시 `aws configure --profile <name>`)

### 1) 가상환경(권장)
```bash
python -m venv .venv
# Windows
.\.venv\Scriptsctivate
# macOS/Linux
source .venv/bin/activate
```

### 2) 의존성 설치
```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3) 서버 실행
```bash
# 방법 A: 모듈 실행(핫리로드)
python -m uvicorn api:app --reload --port 8000

# 방법 B: 파일 실행
python api.py
```

### 4) 테스트
- Swagger UI: http://localhost:8000/docs
- 예시 호출:
  ```bash
  curl "http://localhost:8000/lineage?pipeline=<PIPELINE_NAME>&region=ap-northeast-2&includeLatestExec=true&profile=default"
  ```

> 개발 중에는 `profile`로 로컬 AWS 프로필을 지정할 수 있습니다. 운영 배포에서는 프로필 파라미터를 제거하고 **IAM Role** 사용을 권장합니다.

---

## 🔐 필요한 권한(IAM 예시)

로직은 **조회 권한만** 사용합니다. 최소 권한 예시는 다음과 같습니다(버킷/리소스 ARN은 환경에 맞게 제한 권장).

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

## 🌐 API 사양

### `GET /lineage`

| 파라미터 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| `pipeline` | string | ✅ | `mlops-pipeline` | SageMaker 파이프라인 이름 |
| `region` | string | ✅ | `ap-northeast-2` | AWS 리전 |
| `domain` | string |  | `studio-domain` | 파이프라인의 Tag: `DomainName` 필터 |
| `includeLatestExec` | bool |  | `true` | 최신 실행 정보 보강 여부 |
| `profile` | string |  | `default` | (개발용) 로컬 AWS 프로필명 |

#### 예시
```bash
curl "http://localhost:8000/lineage?pipeline=mlops-pipeline&region=ap-northeast-2&includeLatestExec=true"
```

#### 성공 응답(요약)
```jsonc
{
  "pipeline": { "name": "...", "arn": "...", "lastModifiedTime": "..." },
  "summary": { "overallStatus": "Succeeded", "nodeStatus": { "Succeeded": 3 }, "elapsedSec": 120 },
  "graph": { "nodes": [...], "edges": [...], "artifacts": [...] }
}
```

#### 오류 응답
- `404` – 파이프라인 없음/태그 불일치
- `500` – 권한 부족, S3 접근 실패 등 내부 오류

---

## 🧑‍💻 프론트엔드에서 호출 예시

### fetch (브라우저/React)
```js
const params = new URLSearchParams({
  pipeline: "mlops-pipeline",
  region: "ap-northeast-2",
  includeLatestExec: "true",
});

const res = await fetch(`http://localhost:8000/lineage?${params.toString()}`);
if (!res.ok) throw new Error(await res.text());
const data = await res.json();

// 예: 그래프 데이터
console.log(data.graph.nodes, data.graph.edges, data.graph.artifacts);
```

> CORS 에러가 난다면 `api.py`의 CORS 설정(`allow_origins`)에 프론트 도메인을 추가하세요. 현재 템플릿은 `*`로 열려 있습니다.

---

## 🧪 CLI로 직접 실행(옵션)

`lineage.py`는 CLI로도 동작합니다.

```bash
python lineage.py --region ap-northeast-2 --pipeline-name mlops-pipeline --include-latest-exec --profile default --out lineage.json
```

---

## 🐳 Docker 실행(옵션)

**Dockerfile 예시**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY api.py lineage.py requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

빌드/실행
```bash
docker build -t lineage-api .
docker run --rm -p 8000:8000 lineage-api
# (로컬 자격증명을 써야 한다면 ~/.aws 마운트 및 AWS_PROFILE 환경변수 사용)
```
