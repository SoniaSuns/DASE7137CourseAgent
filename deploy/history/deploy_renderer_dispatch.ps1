$ErrorActionPreference = 'Stop'
$appRoot = Join-Path $PSScriptRoot 'course_agent_app'
$reportRoot = Join-Path $appRoot 'reports\renderer_dispatch_20261009'
$stateRoot = Join-Path $appRoot 'reports\shared_chrome\state'
$tests = Get-Content -LiteralPath (Join-Path $reportRoot 'regression.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $tests.passed) { throw 'Regression tests must pass.' }
$evaluation = Get-Content -LiteralPath (Join-Path $appRoot 'reports\evaluations\latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($evaluation.passed -ne $evaluation.total -or $evaluation.total -ne 30) { throw 'The 30-case evaluation set must pass.' }
foreach ($property in $tests.code_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $appRoot $property.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) {
        throw ('Source changed after regression: ' + $property.Name)
    }
}
$statusPath = Join-Path $appRoot 'reports\shared_chrome\status.json'
$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($status.runtime_version -eq '2026-10-09-renderer-dispatch') { Write-Output 'Save dispatch fix is already deployed.'; exit 0 }
function Test-RecoveryBoundary {
    foreach ($file in Get-ChildItem -LiteralPath $stateRoot -Filter '*.json') {
        if ($file.BaseName -notmatch '^[a-f0-9]{12}$') { continue }
        $state = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.pending_delivery) { throw 'A message delivery is still unresolved.' }
        if ($state.run -eq '155b878a91a5') { continue }
        if ($state.phase -in @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK')) { throw 'Another task is active.' }
    }
    $flow = Get-Content -LiteralPath (Join-Path $stateRoot '29ed78e10116.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($flow.run -ne '155b878a91a5' -or $flow.phase -ne 'NEEDS_READBACK' -or $flow.pending_delivery) { throw 'The investigated task boundary changed.' }
    $observation = $flow.submission_observation
    if ($observation.completion -ne 'pending' -or $observation.post_requests -ne 0 -or $null -ne $observation.response_status -or $observation.click_error_type -ne 'TimeoutError' -or -not $observation.probe_token -or $null -ne $observation.background_requests) { throw 'Submit evidence changed; do not retire this attempt.' }
    if ($flow.results.Count -ne 5 -or $observation.dispatch_probe.ready -ne 'complete') { throw 'Verified receipts or document evidence changed.' }
    foreach ($kind in @('pointerdown','mousedown','click','submit')) {
        if ($observation.dispatch_probe.events.$kind -ne 0) { throw 'An input event was observed; do not retire this attempt.' }
    }
    $queue = ConvertFrom-Json -InputObject (Get-Content -LiteralPath (Join-Path $stateRoot 'inbox.json') -Raw -Encoding UTF8)
    if ($null -ne $queue -and $queue.Count -ne 0) { throw 'New queued work must be preserved explicitly before restart.' }
}
Test-RecoveryBoundary
$oldProcessId = [int]$status.pid
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if (-not $processInfo -or $processInfo.CommandLine -notlike '*v2*run_shared_chrome_agent.py*') { throw 'Reported PID is not the v2 listener.' }
$backup = Join-Path $reportRoot ('deployment-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $stateRoot -Destination (Join-Path $backup 'state') -Recurse
Test-RecoveryBoundary
$latest = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($latest.pid -ne $oldProcessId) { throw 'Listener PID changed; nothing stopped.' }
Stop-Process -Id $oldProcessId
Start-Sleep -Milliseconds 700
if (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) { throw 'Old listener has not stopped.' }
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$process = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-u',$entry,'--run','--retire-unobserved-section-submit','155b878a91a5') -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $reportRoot 'runtime.out.log') -RedirectStandardError (Join-Path $reportRoot 'runtime.err.log')
@{version='2026-10-09-renderer-dispatch';started=(Get-Date).ToString('o');previous_pid=$oldProcessId;launcher_pid=$process.Id;source_run='155b878a91a5';backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment.json') -Encoding UTF8
Write-Output ('Started fixed listener; launcher PID ' + $process.Id + '. Old approval will not be reused.')
