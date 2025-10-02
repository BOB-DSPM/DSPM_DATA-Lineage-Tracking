#!/usr/bin/env bash
# olwrap.sh - 임의 커맨드를 감싸 OpenLineage START/COMPLETE/FAIL 이벤트를 전송
# 의존: curl, jq, uuidgen (macOS는 `brew install jq coreutils` 권장)

set -euo pipefail

MARQUEZ_URL="${MARQUEZ_URL:-http://localhost:5000}"
ENDPOINT="${ENDPOINT:-/api/v1/lineage}"

NAMESPACE="${NAMESPACE:?set NAMESPACE}"
JOB_NAME="${JOB_NAME:?set JOB_NAME}"
INPUTS_JSON="${INPUTS:-[]}"     # 예) '["s3://bucket/raw.csv","file:///tmp/in.parquet"]'
OUTPUTS_JSON="${OUTPUTS:-[]}"   # 예) '["s3://models/xgb:v42"]'
PRODUCER="${PRODUCER:-urn:our-solution:olwrap:1.0}"

RUN_ID="${RUN_ID:-$(uuidgen)}"

now() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

to_datasets_array() {
  # 문자열 배열을 OpenLineage Dataset 배열로 변환
  # s3://* => namespace=s3, file://* => namespace=file, 그 외 => namespace=unknown
  printf '%s' "$1" | jq -c '
    map({
      "namespace":
        (if test("^s3://") then "s3"
         elif test("^file://") then "file"
         elif test("^jdbc:") then "jdbc"
         else "unknown" end),
      "name": .
    })'
}

emit_event() {
  local TYPE="$1"
  local EVENT_TIME="$(now)"
  local INPUTS_DS="$(to_datasets_array "$INPUTS_JSON")"
  local OUTPUTS_DS="$(to_datasets_array "$OUTPUTS_JSON")"

  curl -sS -X POST "${MARQUEZ_URL}${ENDPOINT}" \
    -H "Content-Type: application/json" \
    -d @- <<JSON
{
  "eventType": "${TYPE}",
  "eventTime": "${EVENT_TIME}",
  "producer": "${PRODUCER}",
  "run": { "runId": "${RUN_ID}" },
  "job": { "namespace": "${NAMESPACE}", "name": "${JOB_NAME}" },
  "inputs":  ${INPUTS_DS},
  "outputs": ${OUTPUTS_DS}
}
JSON
  echo
}

# --- 실행 흐름 ---
emit_event "START"

set +e
"$@"
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -eq 0 ]; then
  emit_event "COMPLETE"
else
  emit_event "FAIL"
fi

exit $EXIT_CODE