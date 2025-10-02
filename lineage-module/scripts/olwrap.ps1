<# 
olwrap.ps1 - 임의 명령을 감싸 OpenLineage 이벤트 전송
의존: PowerShell 5+ / 7+, Invoke-RestMethod, [guid]
#>

param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Command
)

$MARQUEZ_URL = $env:MARQUEZ_URL
if ([string]::IsNullOrEmpty($MARQUEZ_URL)) { $MARQUEZ_URL = "http://localhost:5000" }
$ENDPOINT = $env:ENDPOINT
if ([string]::IsNullOrEmpty($ENDPOINT)) { $ENDPOINT = "/api/v1/lineage" }

$NAMESPACE = $env:NAMESPACE
$JOB_NAME  = $env:JOB_NAME

if ([string]::IsNullOrEmpty($NAMESPACE)) { throw "Set NAMESPACE" }
if ([string]::IsNullOrEmpty($JOB_NAME))  { throw "Set JOB_NAME"  }

# JSON array strings like ["s3://...","file:///..."]
$INPUTS_JSON  = $env:INPUTS;  if ([string]::IsNullOrEmpty($INPUTS_JSON))  { $INPUTS_JSON = "[]" }
$OUTPUTS_JSON = $env:OUTPUTS; if ([string]::IsNullOrEmpty($OUTPUTS_JSON)) { $OUTPUTS_JSON = "[]" }
$PRODUCER = $env:PRODUCER;    if ([string]::IsNullOrEmpty($PRODUCER))     { $PRODUCER = "urn:our-solution:olwrap:1.0" }

$RUN_ID = $env:RUN_ID
if ([string]::IsNullOrEmpty($RUN_ID)) { $RUN_ID = [guid]::NewGuid().ToString() }

function NowUtc {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function ToDatasetsArray([string]$json) {
  # PowerShell만으로 간단 치환 (복잡한 규칙은 서버에서 보강)
  $arr = ConvertFrom-Json $json
  $out = @()
  foreach ($item in $arr) {
    if ($item -match "^s3://")     { $ns = "s3" }
    elseif ($item -match "^file://"){ $ns = "file" }
    elseif ($item -match "^jdbc:") { $ns = "jdbc" }
    else                           { $ns = "unknown" }
    $out += @{ namespace = $ns; name = $item }
  }
  return ($out | ConvertTo-Json -Depth 5)
}

function Emit-Event([string]$Type) {
  $payload = [ordered]@{
    eventType = $Type
    eventTime = (NowUtc)
    producer  = $PRODUCER
    run       = @{ runId = $RUN_ID }
    job       = @{ namespace = $NAMESPACE; name = $JOB_NAME }
    inputs    = (ToDatasetsArray $INPUTS_JSON | ConvertFrom-Json)
    outputs   = (ToDatasetsArray $OUTPUTS_JSON | ConvertFrom-Json)
  } | ConvertTo-Json -Depth 8

  Invoke-RestMethod -Method Post -Uri ($MARQUEZ_URL + $ENDPOINT) `
    -ContentType "application/json" -Body $payload | Out-Null
}

Emit-Event "START"

# 실행
& $Command
$EXIT_CODE = $LASTEXITCODE

if ($EXIT_CODE -eq 0) {
  Emit-Event "COMPLETE"
} else {
  Emit-Event "FAIL"
}

exit $EXIT_CODE
