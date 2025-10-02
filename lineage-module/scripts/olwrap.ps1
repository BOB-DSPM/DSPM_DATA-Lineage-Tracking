param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Command
)

# ---- Config ----
$MARQUEZ_URL = $env:MARQUEZ_URL; if ([string]::IsNullOrEmpty($MARQUEZ_URL)) { $MARQUEZ_URL = "http://localhost:5000" }
$ENDPOINT    = $env:ENDPOINT;    if ([string]::IsNullOrEmpty($ENDPOINT))    { $ENDPOINT    = "/api/v1/lineage" }

$NAMESPACE = $env:NAMESPACE; if ([string]::IsNullOrEmpty($NAMESPACE)) { throw "Set NAMESPACE" }
$JOB_NAME  = $env:JOB_NAME;  if ([string]::IsNullOrEmpty($JOB_NAME))  { throw "Set JOB_NAME"  }

# JSON array strings like ["s3://...","file:///..."]
$INPUTS_JSON  = $env:INPUTS;  if ([string]::IsNullOrEmpty($INPUTS_JSON))  { $INPUTS_JSON  = "[]" }
$OUTPUTS_JSON = $env:OUTPUTS; if ([string]::IsNullOrEmpty($OUTPUTS_JSON)) { $OUTPUTS_JSON = "[]" }
$PRODUCER     = $env:PRODUCER;if ([string]::IsNullOrEmpty($PRODUCER))     { $PRODUCER     = "urn:our-solution:olwrap:1.0" }

$RUN_ID = $env:RUN_ID; if ([string]::IsNullOrEmpty($RUN_ID)) { $RUN_ID = [guid]::NewGuid().ToString() }

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }

function Build-DatasetsJson([string]$json) {
  $arr = ConvertFrom-Json $json  # ex) ["s3://...","file:///..."]
  if ($null -eq $arr) { return "[]" }

  $pieces = @()
  foreach ($item in $arr) {
    if ($item -match "^s3://")       { $ns = "s3" }
    elseif ($item -match "^file://") { $ns = "file" }
    elseif ($item -match "^jdbc:")   { $ns = "jdbc" }
    else                             { $ns = "unknown" }

    # 개별 객체를 JSON 문자열로 (항상 객체)
    $pieces += (@{ namespace = $ns; name = $item } | ConvertTo-Json -Compress)
  }

  if ($pieces.Count -eq 0) { return "[]" }
  return "[" + ($pieces -join ",") + "]"
}

function Emit-Event([string]$Type) {
  $inputsJson  = Build-DatasetsJson $INPUTS_JSON
  $outputsJson = Build-DatasetsJson $OUTPUTS_JSON

  $eventTime = NowUtc

  # 페이로드를 "문자열"로 직접 구성 → 단일 원소여도 무조건 배열 유지
  $payload = @"
{
  "eventType":"$Type",
  "eventTime":"$eventTime",
  "producer":"$PRODUCER",
  "run":{"runId":"$RUN_ID"},
  "job":{"namespace":"$NAMESPACE","name":"$JOB_NAME"},
  "inputs": $inputsJson,
  "outputs": $outputsJson
}
"@

  # 디버그 보고 싶으면 주석 해제
  # Write-Host "`n--- PAYLOAD ---`n$payload`n---------------`n"

  Invoke-RestMethod -Method Post -Uri ($MARQUEZ_URL + $ENDPOINT) `
    -ContentType "application/json" -Body $payload | Out-Null
}

# ---- flow ----
Emit-Event "START"

if (-not $Command -or $Command.Count -eq 0) { throw "No command passed. Use: ./olwrap.ps1 -- <exe> <args...>" }
$exe  = $Command[0]
$args = @()
if ($Command.Count -gt 1) { $args = $Command[1..($Command.Count-1)] }

& $exe @args
$EXIT_CODE = $LASTEXITCODE

if ($EXIT_CODE -eq 0) { Emit-Event "COMPLETE" } else { Emit-Event "FAIL" }
exit $EXIT_CODE
