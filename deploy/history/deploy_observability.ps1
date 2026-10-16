param([switch]$Execute,[switch]$WaitForIdle)
$ErrorActionPreference = 'Stop'
$v2Root = [IO.Path]::GetFullPath($PSScriptRoot)
$appRoot = Join-Path $v2Root 'course_agent_app'
$reportRoot = Join-Path $appRoot 'reports\trace_eval_20261009'
$statusPath = Join-Path $appRoot 'reports\shared_chrome\status.json'
$tests = Get-Content -LiteralPath (Join-Path $reportRoot 'regression.json') -Raw | ConvertFrom-Json
if (-not $tests.passed) { throw 'Regression tests must pass before deployment.' }
$evaluation = Get-Content -LiteralPath (Join-Path $appRoot 'reports\evaluations\latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($evaluation.passed -ne $evaluation.total -or $evaluation.total -lt 15) { throw 'The evaluation set must pass before deployment.' }
$active = @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK')
function Test-LiveTaskBusy {
    $stateRoot = Join-Path $appRoot 'reports\shared_chrome\state'
    foreach ($file in Get-ChildItem -LiteralPath $stateRoot -Filter '*.json') {
        if ($file.BaseName -notmatch '^[a-f0-9]{12}$') { continue }
        $flow = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($active -contains $flow.phase -or $flow.pending_delivery) { return $true }
    }
    $inboxPath = Join-Path $stateRoot 'inbox.json'
    if (Test-Path -LiteralPath $inboxPath) {
        $inbox = Get-Content -LiteralPath $inboxPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (@($inbox).Count -gt 0) { return $true }
    }
    return $false
}
while ($true) {
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($status.runtime_version -eq '2026-10-09-trace-eval') { Write-Output 'Trace observability is already deployed.'; exit 0 }
    $busy = $status.queued -ne 0 -or @($status.pending_delivery).Count -gt 0
    foreach ($property in $status.phases.PSObject.Properties) { if ($active -contains $property.Value) { $busy = $true } }
    if (Test-LiveTaskBusy) { $busy = $true }
    if (-not $busy) { break }
    if (-not $WaitForIdle) { throw 'An active task must settle before deployment.' }
    @{status='waiting_for_idle';updated=(Get-Date).ToString('o');phases=$status.phases;watcher_pid=$PID} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment-wait.json') -Encoding UTF8
    Start-Sleep -Seconds 15
}
if (-not $Execute) { Write-Output 'Observability deployment ready.'; exit 0 }
foreach ($property in $tests.code_sha256.PSObject.Properties) {
    $sourcePath = Join-Path $appRoot $property.Name
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) {
        throw ('Source changed since regression checks: ' + $property.Name + '. No process stopped.')
    }
}
$oldProcessId = [int]$status.pid
$oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if (-not $oldProcess -or $oldProcess.CommandLine -notlike '*run_shared_chrome_agent.py*' -or $oldProcess.CommandLine -notlike '*v2*') { throw 'Reported PID is not the v2 listener.' }
$backup = Join-Path $reportRoot ('deployment-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $appRoot 'reports\shared_chrome\state') -Destination (Join-Path $backup 'state') -Recurse
Copy-Item -LiteralPath $statusPath -Destination (Join-Path $backup 'previous-status.json')
# Recheck immediately before stopping; a new active request cancels this attempt.
$latest = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($latest.pid -ne $oldProcessId -or $latest.queued -ne 0 -or @($latest.pending_delivery).Count -gt 0) { throw 'Task boundary changed; no process stopped.' }
foreach ($property in $latest.phases.PSObject.Properties) { if ($active -contains $property.Value) { throw 'Task became active; no process stopped.' } }
if (Test-LiveTaskBusy) { throw 'Durable task or queue became active; no process stopped.' }
Stop-Process -Id $oldProcessId
Start-Sleep -Milliseconds 700
if (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) { throw 'Old listener has not stopped.' }
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$process = Start-Process -FilePath (Join-Path $v2Root '.venv\Scripts\python.exe') -ArgumentList @('-u', $entry, '--run') -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backup 'runtime.out.log') -RedirectStandardError (Join-Path $backup 'runtime.err.log')
@{status='started';started=(Get-Date).ToString('o');previous_pid=$oldProcessId;launcher_pid=$process.Id;version='2026-10-09-trace-eval';report=$backup} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment.json') -Encoding UTF8
Write-Output ('Started observable v2 listener; launcher PID ' + $process.Id)
