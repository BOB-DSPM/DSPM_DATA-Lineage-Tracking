# MLOps Platform Setting Guide (OpenLineage → Marquez)

> 목적: 어떤 MLOps 플랫폼에서도 **OpenLineage 이벤트**를 안정적으로 발행하여 **Marquez(API 전용)**로 수집·저장·그래프화하고, 우리 대시보드에서 **Marquez REST**를 조회해 시각화할 수 있도록 최소/권장 설정을 정리합니다.

## 공통 사전값 (모든 플랫폼)

- **수신 URL**: `OPENLINEAGE_URL = http://<HOST>:5000`  
  *(엔드포인트는 대부분 자동 `/api/v1/lineage`)*
- **네임스페이스**: `OPENLINEAGE_NAMESPACE = <env/team>` (예: `dev`)
- **(선택) 헤더 토큰**: `OPENLINEAGE_API_KEY` *(ALB/WAF 등에서 요구 시)*
- **본 래퍼 사용 시**  
  `MARQUEZ_URL=http://<HOST>:5000`, `NAMESPACE=dev`, `JOB_NAME=<job-id>`, `INPUTS='["uri"]'`, `OUTPUTS='["uri"]'`

> 최소 접근은 **작업 커맨드를 래퍼(olwrap.ps1/olwrap.sh)로 감싸기**, 권장 접근은 **플랫폼용 OpenLineage 통합(에이전트/프로바이더/리스너)** 설치

---

# 1) Apache Airflow

## A. 최소(이미 가능)
- DAG 태스크 커맨드를 **`olwrap.ps1` / `olwrap.sh`** 로 감싸서 실행 → `START/COMPLETE/FAIL` + `inputs/outputs` 수동 지정

## B. 권장(정확도 ↑)

### 1) 설치
```bash
pip install apache-airflow-providers-openlineage
```

### 2) 설정 (둘 중 하나)

**airflow.cfg**
```
[openlineage]
transport = {"type":"http","url":"http://<HOST>:5000","endpoint":"api/v1/lineage"}
namespace = dev
# (선택) apiKey, timeout, verifyTls
```

**또는 환경변수**
```bash
export OPENLINEAGE_URL="http://<HOST>:5000"
export OPENLINEAGE_NAMESPACE="dev"
# export OPENLINEAGE_API_KEY="xxx"
```

### 3) 기대 효과
- 주요 오퍼레이터(BigQuery/Snowflake/Redshift/File/SQL 등)에서 **입출력 자동 추출**
- 태스크 재시도/SLAs/업스트림 관계가 **Run/Job 메타**로 반영

### 4) 검증 체크
- `GET /api/v1/namespaces/dev/jobs` 에 DAG/Task 등록 확인
- `GET /api/v1/namespaces/dev/jobs/<dag_id>.<task_id>/runs` 상태 확인
- `/api/v1/search?q=<테이블/경로>` 로 데이터셋 인덱스 확인

> **주의**: 커스텀 오퍼레이터는 extractor가 없을 수 있음 → 필요 시 inputs/outputs 수동 설정 또는 extractor 작성

---

# 2) Spark (온프레미스 / 표준 spark-submit)

## A. 최소
- 잡 실행 커맨드를 **`olwrap.sh`** 로 감싸고 `INPUTS/OUTPUTS`에 주요 URI 명시

## B. 권장(정확도 ↑)

### 1) Spark Agent JAR 탑재
- `openlineage-spark-<version>.jar` (드라이버가 접근 가능한 경로)

### 2) spark-submit 예시
```bash
spark-submit   --conf spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener   --conf spark.openlineage.transport.type=http   --conf spark.openlineage.url=http://<HOST>:5000   --conf spark.openlineage.namespace=dev   --jars /path/to/openlineage-spark-<ver>.jar   your_job.py
```
*(Scala/Java JAR 잡도 동일 conf)*

### 3) 기대 효과
- DataFrame read/write, Spark SQL의 **소스/싱크 자동 추적**
- parquet/csv/jdbc/Delta 등 커넥터 인식

### 4) 검증 체크
- `/api/v1/search?q=<테이블/경로>` 데이터셋 확인
- `/api/v1/lineage?nodeId=...` 업/다운스트림 확인

> **주의**: 사용자 정의 커넥터나 UDF로 I/O가 감춰지면 수동 보강 필요

---

# 3) Amazon EMR (Spark on EMR)

## A. 최소
- **Step** 커맨드를 `olwrap.sh`로 감싸기 (AMI 기반 리눅스 환경이므로 bash 래퍼 사용)

## B. 권장(정확도 ↑)

### 1) 에이전트 JAR S3 업로드
- `s3://<bucket>/jars/openlineage-spark-<ver>.jar`

### 2) EMR Step 예시
```bash
spark-submit   --conf spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener   --conf spark.openlineage.transport.type=http   --conf spark.openlineage.url=http://<HOST>:5000   --conf spark.openlineage.namespace=dev   --jars s3://<bucket>/jars/openlineage-spark-<ver>.jar   s3://<code-bucket>/jobs/etl.py
```
- 클러스터 수준 **부트스트랩 액션**으로 공통 JAR/Conf 배포하면 재사용성 ↑

### 3) 기대 효과/검증
- Spark와 동일. EMR 메타(클러스터/스텝 ID)는 선택적으로 facet로 주입 가능

> **주의**: 보안 그룹/프록시로 `http://<HOST>:5000` 접근 가능해야 함

---

# 4) Databricks

## A. 최소
- 노트북/잡 스텝에서 수동 emit 또는 작업 커맨드를 `olwrap.sh`로 감싸기(컨테이너/클러스터 이미지에 포함 시)

## B. 권장(정확도 ↑)

### 1) 클러스터 라이브러리로 Agent JAR 설치
- DBFS/Workspace에 업로드 후 **Cluster Libraries**에 추가

### 2) 클러스터 또는 잡 Spark Conf
```
spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener
spark.openlineage.transport.type=http
spark.openlineage.url=http://<HOST>:5000
spark.openlineage.namespace=dev
```
*(Init script로 conf 주입하면 표준화 쉬움)*

### 3) 기대 효과
- Delta/SQL, Auto Loader 등에서 **입출력 자동 추출**
- 잡/클러스터 컨텍스트와 함께 라인리지 강화

### 4) 검증
- `/api/v1/search` 로 Delta 테이블/경로 인덱스 확인
- `/api/v1/lineage` 로 그래프 확인

> **주의**: Notebook Magic, 인증된 외부 시스템 I/O는 일부 수동 보강 필요

---

# 5) Kubeflow Pipelines (KFP)

## A. 최소
- **컴포넌트 컨테이너의 command**를 `olwrap.sh`로 감싸기  
  *(이미지에 `/app/olwrap.sh`를 포함)*

```yaml
implementation:
  container:
    image: <your-image>
    command: ["bash","-lc"]
    args:
      - |
        export MARQUEZ_URL=http://<HOST>:5000
        export NAMESPACE=dev
        export JOB_NAME={{workflow.name}}.{{pod.name}}   # 파이프라인/스텝 식별 결합
        export INPUTS='["s3://bucket/in.csv"]'
        export OUTPUTS='["s3://bucket/out.csv"]'
        /app/olwrap.sh python /app/component.py --arg foo
```

## B. 권장(정확도 ↑)
- Python component 내부에서 **OpenLineageClient**로 facet 주입:
  - 실행 파라미터, 아티팩트 메타, 모델 버전 등
- 파이프라인/런 ID를 **job.run facets**에 포함하여 추적성 향상

## 검증
- `/api/v1/namespaces/{ns}/jobs` 에 `<pipeline>.<component>` 형식의 잡 생성
- `/api/v1/namespaces/{ns}/jobs/<job>/runs` 에서 SUCCESS/FAIL 확인
- `/api/v1/search` & `/api/v1/lineage` 로 파일/GCS/S3 의존 관계 확인

> **주의**: KFP Artifact Store(GCS/S3) URI를 일관되게 넣어야 그래프 연결이 선명해짐

---

# 6) AWS SageMaker (Processing / Training / Batch Transform)

## A. 최소
- **entrypoint**를 `olwrap.sh`로 감싸고 `INPUTS/OUTPUTS`에 채널/아티팩트 S3 경로 지정

예) Estimator (개념)
```python
estimator = Estimator(
  image_uri=...,
  entry_point="train.py",  # 컨테이너에 포함
  # 실제 실행시:
  # command=["/bin/bash","-lc",
  #   "export MARQUEZ_URL=http://<HOST>:5000;
  #    export NAMESPACE=dev;
  #    export JOB_NAME=sagemaker-train-$(date +%s);
  #    export INPUTS='[\"s3://<bucket>/input/\"]';
  #    export OUTPUTS='[\"s3://<bucket>/model/output.tar.gz\"]';
  #    /app/olwrap.sh python /app/train.py --epochs 5"
  # ],
)
```

## B. 권장(정확도 ↑)
- `train.py` / `process.py`에서 **OpenLineageClient**로:
  - START 시 **하이퍼파라미터**, **코드/이미지 버전**, **데이터 채널 URI** facets
  - COMPLETE 시 **모델 아티팩트 S3**, **평가 메트릭** facets
- Job/Trial/Experiment ID를 run facets에 포함

## 검증
- `/api/v1/namespaces/{ns}/jobs` 에 `sagemaker-...` 잡 생성
- `/api/v1/namespaces/{ns}/jobs/<job>/runs` 에서 상태 확인
- `/api/v1/search` 로 입력 채널/모델 아티팩트 노출
- `/api/v1/lineage` 로 데이터 → 학습 → 모델 아웃풋 연결 확인

> **주의**: SageMaker 스튜디오/노트북에서 프록시/보안 정책으로 외부 URL 접근이 막힐 수 있음 → VPC 엔드포인트/ALB 보안그룹 조정

---

## 공통 “강화 포인트”

- **거버넌스 facet**(권장): 래퍼 또는 코드에서 아래 형태로 주입
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
- **네이밍 표준**:
  - Job namespace: `env.team.domain`
  - Job name: `pipeline/task`
  - dataset name: 절대 URI (`s3://…`, `jdbc://…`, `file://…`)
  - runId: UUIDv4 (재시도 정책 문서화)
- **보안**: ALB+HTTPS, (선택) HMAC/JWT 헤더 검증
- **신뢰성**: 실패 시 재시도/backoff, Composite 전송(HTTP+Kafka)로 유실 방지(운영 단계)

---

### 빠른 검증 체크리스트
- [ ] `GET /api/v1/namespaces` 응답 OK
- [ ] `/jobs`에 플랫폼 잡/태스크가 보임
- [ ] `/runs`에 SUCCESS/FAIL 상태가 기록됨
- [ ] `/search`에 주요 데이터셋이 인덱싱됨
- [ ] `/lineage`로 업/다운스트림 그래프 연결 확인

> 모든 플랫폼은 **최소(래퍼)** → **권장(네이티브 에이전트/리스너/프로바이더)** 단계로 점진 도입 가능하다.
