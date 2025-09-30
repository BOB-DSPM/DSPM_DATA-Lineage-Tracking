# DSPM Data Lineage MVP (OpenLineage + Marquez)

> **목적**: 샘플 데이터 기준의 순수 데이터 라인리지 기록에만 우선 집중하여 구현 진행 중

## 1. 기능 개요

### 1) 최소 라인리지 기록
- **OpenLineage 표준 이벤트**(START/COMPLETE)만 사용하여 각 단계의 **입력 데이터셋 → 잡(job) → 출력 아티팩트**를 기록한다.
- 각 단계는 **두 번의 이벤트**로 구성되어 있다.
  - `START`: “잡 X가 입력 Y로 시작했다”
  - `COMPLETE`: “잡 X가 출력 Z로 종료됐다”
- 이 두 이벤트로 **Y → X → Z**의 엣지(관계)가 생성되어 **추적/재현/감사**가 가능해진다.

### 2) 저장과 조회
- **저장(Write)**: `lineage_emitter/emitter.py`가 **Marquez** API(`POST /api/v1/lineage`)로 이벤트를 전송한다.
- **조회(Read)**:
  - `lineage-gateway/app.py`를 통해 **그래프/경로** 조회용 간단한 엔드포인트(`/lineage/graph`, `/lineage/path`)를 제공한다.
  - 또는 Marquez의 기본 API를 사용해 네임스페이스/잡/데이터셋 정보를 조회한다.

## 2. 레포지토리 구성 (현재)

```
DSPM_DATA-Lineage-Tracking/
├─ lineage-emitter-py/
│  ├─ lineage_emitter/emitter.py       # 공용 이벤트 발사 라이브러리 (Python)
│  └─ requirements.txt
├─ lineage-gateway/
│  ├─ app.py                           # 조회/프록시 API (FastAPI)
│  ├─ Dockerfile
│  └─ requirements.txt
├─ lineage-infra/
│  ├─ docker-compose.dev.yml           # 로컬 실행: Postgres + Marquez + Gateway + Producer
│  ├─ marquez-config.yml               # Marquez DB 설정 (중요)
│  └─ sample/send_event.py             # 샘플 이벤트 발사 스크립트
├─ testdata/
│  ├─ ml/train.csv
│  ├─ ml/eval.csv
│  └─ pii/emails_kr.csv, rrn_mix.csv
└─ (zip 안에 .git 포함)
```

## 3. 동작 방식 (아키텍처)

```
[샘플 파일(testdata)] 
    └─(해시 기반 ID 생성)→ [emit_minimal.py] 
        └─(START/COMPLETE)→ [Marquez(API)] → [PostgreSQL]
                               └─(선택)→ [lineage-gateway] → /lineage/graph, /lineage/path
```

- **데이터셋 ID**: `file::<절대경로>@sha256:<내용해시>` 형태로 생성하여 **같은 파일이면 동일 ID**가 되도록 함 (라인리지 연결 안정화).
- **네임스페이스**: `OL_NAMESPACE` 환경변수로 논리 경계를 구분(예: `your.team.or.project`).

## 4. 설정 값

- `OL_NAMESPACE`: 라인리지 네임스페이스(기본: `dspm.mlops` 또는 사용자가 지정한 값).
- `MARQUEZ_ENDPOINT`:
  - 호스트에서 호출: `http://localhost:5000/api/v1/lineage`
  - Docker 네트워크 내부에서 호출: `http://marquez:5000/api/v1/lineage`

## 5. 실행 순서 (PowerShell 기준)

### 5.1 인프라 기동
```powershell
cd lineage-infra
docker compose -f .\compose.min.yml up -d --build
```
- Marquez API: `http://localhost:5000/api/v1/lineage`
- (선택) lineage-gateway: `http://localhost:8000`

### 5.2 에미터 의존성 설치
```powershell
pip install -r .\lineage-emitter-py
equirements.txt
```

### 5.3 최소 이벤트 송신
```powershell
python .\scripts\emit_minimal.py
```
- 기본 입력: `.	estdata\ml	rain.csv`  
- 잡 이름/산출물 ID는 `emit_minimal.py` 내부에서 수정 가능

---

## 6. 이벤트 형태(참고)
> 실제 전송은 `lineage_emitter/emitter.py`의 `start_run(...)`, `complete_run(...)`가 알아서 구성함.
```json
// START
{
  "eventType": "START",
  "eventTime": "2025-09-30T15:00:00Z",
  "run": { "runId": "uuid-1", "facets": {} },
  "job": { "namespace": "your.team.or.project", "name": "train_model_min" },
  "inputs": [
    { "namespace": "file", "name": "file::C:/abs/path/train.csv@sha256:..." }
  ],
  "outputs": []
}
```
```json
// COMPLETE
{
  "eventType": "COMPLETE",
  "eventTime": "2025-09-30T15:10:00Z",
  "run": { "runId": "uuid-1", "facets": {} },
  "job": { "namespace": "your.team.or.project", "name": "train_model_min" },
  "inputs": [],
  "outputs": [
    { "namespace": "ml-registry", "name": "model:fraud/1.0.0" }
  ]
}
```

## 7. 라인리지 확인 방법

### 7.1 Marquez API로 직접 확인
```powershell
curl http://localhost:5000/api/v1/namespaces
curl "http://localhost:5000/api/v1/jobs?namespace=your.team.or.project"
curl "http://localhost:5000/api/v1/datasets?namespace=file"
```

### 7.2 lineage-gateway(선택)로 확인
> `<ID>`에는 데이터셋 ID(`file::...@sha256:...`), 잡 이름(`train_model_min`), 모델 ID(`model:fraud/1.0.0`) 등을 사용할 수 있음.
- **그래프(하향/상향)**:  
  `GET http://localhost:8000/lineage/graph?entity=<ID>&direction=down&depth=5`
- **두 점 최단 경로**:  
  `GET http://localhost:8000/lineage/path?frm=<FROM>&to=<TO>&depth=10`

## 8. 앞으로 해야 할 일 (Next Steps)

1. **MVP 검증**: `emit_minimal.py` 실행 → Marquez에서 네임스페이스/잡/데이터셋 조회로 레코드 확인 → gateway로 라인리지 결과 확인
2. **실제 파이프라인 연결**: 전처리/학습/평가 등 각 단계에 `start_run`/`complete_run` 삽입. Job 이름/아티팩트 ID 표준화
3. **ID 안정화 전략 문서화**: 파일은 경로+해시, 모델/지표는 일관된 네이밍 규칙 수립
4. 이외 기타 등등....
