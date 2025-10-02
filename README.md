# Data Lineage (OpenLineage + Marquez)

> MLOps 플랫폼에 관계 없이 **OpenLineage 표준 이벤트**기반으로 **Marquez**가 라인리지를 저장/버저닝/그래프화하고, **솔루션 대시보드**를 통해 Marquez **REST API**로 이를 조회·표시한다. Marquez 기본 UI는 사용하지 않으며, 저장·질의 API만 활용한다.

## 목차
- [레포지토리 구성](#레포지토리-구성)
- [아키텍처](#아키텍처)
- [사전 준비물](#사전-준비물)
- [빠른 시작 (로컬 기동)](#빠른-시작-로컬-기동)
- [이벤트 발행 래퍼 (플랫폼 무관)](#이벤트-발행-래퍼-플랫폼-무관)
- [기능 검증 (Runs / Search / Lineage)](#기능-검증-runs--search--lineage)
- [대시보드 연동: REST 계약](#대시보드-연동-rest-계약)
- [컨벤션 & 확장 포인트](#컨벤션--확장-포인트)
- [요약](#요약)

## 레포지토리 구성

```
lineage-module/
├─ docker-compose.yml          # 로컬 기동: PostgreSQL + Marquez(API)
├─ marquez-config.yml          # Marquez DB/포트 설정 (컨테이너 마운트용)
├─ scripts/
│  ├─ olwrap.ps1               # Windows/PowerShell 범용 이벤트 래퍼
│  └─ olwrap.sh                # Linux/macOS 범용 이벤트 래퍼
├─ facets/
│  └─ governance-1.0.json      # 거버넌스 커스텀 facet 스키마
├─ docs/                       # MLOps 플랫폼별 추가 문서들
└─ README.md
```

- **필수 코어**: `docker-compose.yml`, `marquez-config.yml`, `scripts/olwrap.ps1`  
- **권장**: `scripts/olwrap.sh`(리눅스/CI 대비), `facets/governance-1.0.json`(거버넌스 표준화)

## 아키텍처

```
[ Producer(잡/파이프라인) ]
     │
     │ OpenLineage 이벤트 (HTTP/JSON: START/COMPLETE/FAIL, inputs/outputs, facets)
     ▼
[ Marquez API ]  ←→  [ PostgreSQL ]
     │
     │  Jobs / Runs / Datasets / Lineage REST
     ▼
[ 솔루션 대시보드 ]
  - 라인리지 그래프(업/다운스트림)
  - 런 타임라인/상태
  - 데이터셋 검색/필터
```

- **Producer**: 잡 시작/종료 시점에 **OpenLineage** 이벤트를 HTTP로 전송  
- **Marquez**: 수신·저장·버저닝·그래프 연결(라인리지)  
- **대시보드**: **Marquez REST**를 Pull하여 렌더(마르퀘즈 UI 미사용)

## 사전 준비물

- Docker / Docker Compose
- Windows: PowerShell 5+ 또는 7+
- Linux/macOS: `bash`, `curl`, `jq` (권장), `uuidgen` (권장)  
  *macOS: `brew install jq coreutils`*

`.env.example`를 참고해 `.env`를 만들면 편함

```dotenv
# .env 예시
MARQUEZ_PORT=5000
POSTGRES_PORT=5432
POSTGRES_DB=marquez
POSTGRES_USER=marquez
POSTGRES_PASSWORD=marquez
```

## 빠른 시작 (로컬 기동)

레포 루트(`lineage-module/`)에서:

```bash
# Windows / Linux / macOS 공통
docker compose up -d
```

기동 확인:
> `marquez-config.yml`이 컨테이너에 마운트되어 있으며 DB 호스트는 도커 서비스명(`postgres`)를 사용한다.
```powershell
# Windows PowerShell
Invoke-RestMethod -Uri "http://localhost:5000/api/v1/namespaces" -Method GET
# → 빈 배열([]) 또는 정상 JSON이면 OK
```

## 이벤트 발행 래퍼 (플랫폼 무관)

> 래퍼는 **START → (사용자 작업 실행) → COMPLETE/FAIL** 순으로 OpenLineage 이벤트를 자동 전송한다.  
> `INPUTS`/`OUTPUTS`는 **JSON 배열 문자열**이며, URI 스킴에 따라 dataset **namespace**가 자동 분류된다(예: `s3://…` → `s3`).

### Windows (PowerShell)

```powershell
cd lineage-module

$env:MARQUEZ_URL = "http://localhost:5000"
$env:NAMESPACE   = "dev"                             # 잡 네임스페이스(환경/팀)
$env:JOB_NAME    = "demo-job"                        # 잡 이름
$env:INPUTS      = '["s3://datalake/raw/customers.parquet"]'
$env:OUTPUTS     = '["s3://curated/customers_clean.parquet"]'

# 실제 작업을 감싸서 실행: 성공 시 COMPLETE, 실패 시 FAIL 자동 전송
./scripts/olwrap.ps1 -- powershell -Command "Set-Content -Path $env:TEMP\out.txt -Value 'ok'"
```

### Linux / macOS (Bash)

```bash
cd lineage-module

export MARQUEZ_URL=http://localhost:5000
export NAMESPACE=dev
export JOB_NAME=demo-job
export INPUTS='["s3://datalake/raw/customers.parquet"]'
export OUTPUTS='["s3://curated/customers_clean.parquet"]'

./scripts/olwrap.sh bash -lc 'echo ok > /tmp/out.txt'
```

## 기능 검증 (Runs / Search / Lineage)

### Runs (상태 기록)
> 참고: **Job 네임스페이스(`dev`)**와 **Dataset 네임스페이스(`s3`)**는 다를 수 있다.  
> 데이터셋 목록은 `GET /api/v1/namespaces/s3/datasets`에서 확인된된다.

```powershell
Invoke-RestMethod -Uri "http://localhost:5000/api/v1/namespaces/dev/jobs/demo-job/runs" -Method GET
```

- **예상 결과**: 방금 실행한 Run이 보이고, `state=COMPLETED` (또는 실패 시 `FAILED`)

### Search (데이터셋 인덱싱)

```powershell
Invoke-RestMethod -Uri "http://localhost:5000/api/v1/search?q=customers" -Method GET
```

- **예상 결과**:
  - `s3://curated/customers_clean.parquet`
  - `s3://datalake/raw/customers.parquet`  
    가 **type=DATASET**, **namespace=s3**로 조회


### Lineage (업/다운스트림 그래프)

`/search` 응답의 `nodeId`를 사용합니다. 예:  
`dataset:s3:s3://curated/customers_clean.parquet`

```powershell
$nodeId = "dataset:s3:s3://curated/customers_clean.parquet"
$enc    = [System.Uri]::EscapeDataString($nodeId)
Invoke-RestMethod -Uri "http://localhost:5000/api/v1/lineage?nodeId=$enc&depth=3" -Method GET
```

- **예상 결과**: 업스트림에 `s3://datalake/raw/customers.parquet`, 연결 잡 `demo-job` 등 노드/엣지가 표시

## 대시보드 연동: REST 계약

우리 솔루션 대시보드는 아래 **Marquez REST**를 Polling/Pull 방식으로 사용한다.

### Jobs & Runs
- `GET /api/v1/namespaces/{ns}/jobs`
- `GET /api/v1/namespaces/{ns}/jobs/{job}`
- `GET /api/v1/namespaces/{ns}/jobs/{job}/runs`

### Datasets & Search
- `GET /api/v1/namespaces/{ns}/datasets` *(예: ns=`s3`)*
- `GET /api/v1/search?q={keyword}`

### Lineage Graph
- `GET /api/v1/lineage?nodeId=<urlencoded>&depth=K`  
  *(nodeId 예: `dataset:s3:s3://curated/customers_clean.parquet`)*


## 컨벤션 & 확장 포인트

### 네이밍/식별자
- `namespace`(job): `env.team.domain` (예: `prod.ds.marketing`)
- `job.name` : `pipeline/task` (예: `feature-store/build_user_features`)
- `runId` : UUIDv4 (재시도 정책은 팀 규칙)
- dataset `name` : 절대식별 가능 URI (예: `s3://…`, `jdbc:…`, `file://…`)
- `producer` : `urn:our-solution:<component>:<version>`

### 거버넌스(선택 facet)
`facets/governance-1.0.json` 스키마에 맞춰 이벤트에 facet을 첨부하면, 대시보드에서 **보호/정책**을 시각화할 수 있습니다.

```json
{
  "governance": {
    "_schemaURL": "https://our-solution.io/facets/governance-1.0.json",
    "pii": true,
    "classification": "restricted",
    "retention": "1y",
    "encryption": "AES-256-at-rest"
  }
}
```

## 요약

- **완료된 범위**: 오픈소스(OpenLineage + Marquez)만으로 **라인리지 수집·저장·조회** 코어 구현 및 로컬 검증
- **MLOps-무관**: 래퍼(PS1/SH)로 어떤 잡이든 즉시 이벤트 발행하여 테스트 가능
- **대시보드-친화**: Marquez REST → 그래프/타임라인/검색 UI로 연결 가능
