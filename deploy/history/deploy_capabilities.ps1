param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$v2Root = [IO.Path]::GetFullPath($PSScriptRoot)
$appRoot = Join-Path $v2Root 'course_agent_app'
$reportRoot = Join-Path $appRoot 'reports\implementation_20261008'
$statusPath = Join-Path $appRoot 'reports\shared_chrome\status.json'
$tests = Get-Content -LiteralPath (Join-Path $reportRoot 'regression.json') -Raw | ConvertFrom-Json
if (-not $tests.passed) { throw 'Regression tests must pass before deployment.' }
$status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
$active = @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK')
foreach ($property in $status.phases.PSObject.Properties) {
    if ($active -contains $property.Value) { throw 'A task is active; wait for a safe task boundary before deployment.' }
}
if ($status.queued -ne 0 -or @($status.pending_delivery).Count -gt 0) { throw 'Queued work or delivery must settle before deployment.' }
$oldProcessId = [int]$status.pid
$oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if (-not $oldProcess -or $oldProcess.CommandLine -notlike '*run_shared_chrome_agent.py*' -or $oldProcess.CommandLine -notlike '*v2*') {
    throw 'The reported listener PID does not identify the v2 entry.'
}
if (-not $Execute) { Write-Output 'Deployment ready at an idle task boundary.'; exit 0 }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $reportRoot ('deployment-' + $stamp)
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $appRoot 'reports\shared_chrome\state') -Destination (Join-Path $backup 'state') -Recurse
Copy-Item -LiteralPath $statusPath -Destination (Join-Path $backup 'previous-status.json')
Stop-Process -Id $oldProcessId
Start-Sleep -Milliseconds 700
$pythonPath = Join-Path $v2Root '.venv\Scripts\python.exe'
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$process = Start-Process -FilePath $pythonPath -ArgumentList @('-u', $entry, '--run') -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backup 'runtime.out.log') -RedirectStandardError (Join-Path $backup 'runtime.err.log')
@{started=(Get-Date).ToString('o');previous_pid=$oldProcessId;launcher_pid=$process.Id;version='2026-10-08-capabilities';state_preserved=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment.json') -Encoding UTF8
Write-Output ('Started updated v2 listener; launcher PID ' + $process.Id)
