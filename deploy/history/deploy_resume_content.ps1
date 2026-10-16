param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$v2Root = [IO.Path]::GetFullPath($PSScriptRoot)
$appRoot = Join-Path $v2Root 'course_agent_app'
$reportRoot = Join-Path $appRoot 'reports\resume_content_20261009'
$statusPath = Join-Path $appRoot 'reports\shared_chrome\status.json'
$tests = Get-Content -LiteralPath (Join-Path $reportRoot 'regression.json') -Raw | ConvertFrom-Json
if (-not $tests.passed) { throw 'Regression tests must pass before deployment.' }
$live = Get-Content -LiteralPath (Join-Path $reportRoot 'live_preflight.json') -Raw | ConvertFrom-Json
if (-not $live.move_preflight_passed -or -not $live.bytes_match -or $live.save_or_move_called) { throw 'Live read-only validation is incomplete.' }
$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
$active = @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK')
foreach ($property in $status.phases.PSObject.Properties) {
    if ($active -contains $property.Value) { throw 'A task is active; deploy at an idle task boundary.' }
}
if ($status.queued -ne 0 -or @($status.pending_delivery).Count -gt 0) { throw 'Queued work or delivery must settle.' }
$oldProcessId = [int]$status.pid
$oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if (-not $oldProcess -or $oldProcess.CommandLine -notlike '*run_shared_chrome_agent.py*' -or $oldProcess.CommandLine -notlike '*v2*') { throw 'The reported PID does not identify the v2 listener.' }
if (-not $Execute) { Write-Output 'Resume/content deployment ready.'; exit 0 }
$backup = Join-Path $reportRoot ('deployment-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $appRoot 'reports\shared_chrome\state') -Destination (Join-Path $backup 'state') -Recurse
Copy-Item -LiteralPath $statusPath -Destination (Join-Path $backup 'previous-status.json')
Stop-Process -Id $oldProcessId
Start-Sleep -Milliseconds 700
if (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) { throw 'Old listener has not stopped.' }
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$process = Start-Process -FilePath (Join-Path $v2Root '.venv\Scripts\python.exe') -ArgumentList @('-u', $entry, '--run') -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backup 'runtime.out.log') -RedirectStandardError (Join-Path $backup 'runtime.err.log')
@{started=(Get-Date).ToString('o');previous_pid=$oldProcessId;launcher_pid=$process.Id;version='2026-10-09-resume-content';report=$backup} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment.json') -Encoding UTF8
Write-Output ('Started repaired v2 listener; launcher PID ' + $process.Id)
