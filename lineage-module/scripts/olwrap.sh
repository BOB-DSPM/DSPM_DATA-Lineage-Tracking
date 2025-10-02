#!/usr/bin/env bash
# olwrap.sh - 임의 커맨드를 감싸 OpenLineage START/COMPLETE/FAIL 이벤트를 전송
# deps: curl, jq, (uuidgen 또는 /proc/sys/kernel/random/uuid 또는 openssl)

set -euo pipefail

# --------- deps check ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 not found" >&2; exit 127; }; }
need curl
need jq

# --------- config ----------
MARQUEZ_URL="${MARQUEZ_URL:-http://localhost:5000}"
ENDPOINT="${ENDPOINT:-/api/v1/lineage}"

: "${NAMESPACE:?set NAMESPACE}"
: "${JOB_NAME:?set JOB_NAME}"

INPUTS_JSON="${INPUTS:-[]}"     # 예: '["s3://bucket/raw.csv","file:///tmp/in.parquet"]'
OUTPUTS_JSON="${OUTPUTS:-[]}"   # 예: '["s3://models/xgb:v42"]'
PRODUCER="${PRODUCER:-urn:our-solution:olwrap:1.0}"

# RUN_ID 생성 (uuidgen 없을 때 대체)
if command -v uuidgen >/dev/null 2>&1; then
  RUN_ID="${RUN_ID:-$(uuidgen)}"
elif [ -r /proc/sys/kernel/random/uuid ]; then
  RUN_ID="${RUN_ID:-$(cat /proc/sys/kernel/random/uuid)}"
else
  need openssl
  RUN_ID="${RUN_ID:-$(openssl rand -hex 16)}"
fi

now() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# --------- helpers ----------
# 입력이 배열이 아니어도(단일 문자열 등) 안전하게 배열로 만들고 Dataset 객체 배열로 변환
to_datasets_array() {
  # shell arg: raw JSON (string or array); convert -> array of {namespace,name}
  # - invalid JSON이면 빈 배열로 처리
  printf '%s' "${1:-[]}" | jq -c '
    try (
      (if type=="array" then . else [.] end)
      | map(
          if type=="string" then .
          else
            # string이 아니면 문자열로 cast
            tostring
          end
        )
      | map({
          namespace:
            (if test("^s3://") then "s3"
             elif test("^file://") then "file"
             elif test("^jdbc:") then "jdbc"
             else "unknown" end),
          name: .
        })
    ) catch []'
}

emit_event() {
  local TYPE="$1"
  local EVENT_TIME
  EVENT_TIME="$(now)"
  local INPUTS_DS OUTPUTS_DS
  INPUTS_DS="$(to_datasets_array "$INPUTS_JSON")"
  OUTPUTS_DS="$(to_datasets_array "$OUTPUTS_JSON")"

  # 디버그 하고 싶으면 주석 해제
  # echo "DEBUG payload $TYPE:" >&2
  # jq -n --arg t "$TYPE" --arg et "$EVENT_TIME" --arg p "$PRODUCER" \
  #    --arg ns "$NAMESPACE" --arg job "$JOB_NAME" \
  #    --argjson ins "$INPUTS_DS" --argjson outs "$OUTPUTS_DS" \
  #    '{eventType:$t,eventTime:$et,producer:$p,run:{runId:$ENV.RUN_ID},job:{namespace:$ns,name:$job},inputs:$ins,outputs:$outs}' >&2

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

# 신호/에러 시 FAIL 보내기 (실패 시에도 이벤트 남도록)
fail_trap() {
  # set -e일 때 START만 보내고 중간에 죽는 상황 방지
  emit_event "FAIL" || true
  exit 1
}
trap fail_trap INT TERM
# 명령 실행 구간에서만 ERR 트랩 적용
# (사전 검증 단계에서의 jq/curl 실패는 바로 종료되도록 유지)

# --------- flow ----------
emit_event "START"

set +e
# 명령 실행
"$@"
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -eq 0 ]; then
  emit_event "COMPLETE"
else
  emit_event "FAIL"
fi

exit $EXIT_CODE
