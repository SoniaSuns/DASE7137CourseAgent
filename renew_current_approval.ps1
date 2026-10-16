param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$v2Root = [IO.Path]::GetFullPath($PSScriptRoot)
$appRoot = Join-Path $v2Root 'course_agent_app'
$reportRoot = Join-Path $appRoot 'reports\approval_delivery_20261008'
$statusPath = Join-Path $appRoot 'reports\shared_chrome\status.json'
$statePath = Join-Path $appRoot 'reports\shared_chrome\state\29ed78e10116.json'
$tests = Get-Content -LiteralPath (Join-Path $reportRoot 'regression.log') -Raw
if ($tests -notmatch '(?m)^OK\s*$' -or $tests -match '(?m)^(FAILED|ERROR|FAIL):?') { throw 'Regression must pass before renewal.' }
$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($state.run -ne 'a881c3557b99' -or $state.phase -ne 'APPROVAL' -or $state.approval_sent -ne $false -or @($state.selected).Count -ne 32 -or @($state.results).Count -ne 0 -or $state.PSObject.Properties.Name -contains 'write_started') {
    throw 'The exact undelivered and unwritten approval is no longer current.'
}
if ($status.queued -ne 0) { throw 'Queued work must settle before this targeted restart.' }
foreach ($property in $status.phases.PSObject.Properties) {
    if ($property.Name -ne 'test approval' -and @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK') -contains $property.Value) { throw 'Another task is active.' }
}
$oldProcessId = [int]$status.pid
$oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if (-not $oldProcess -or $oldProcess.CommandLine -notlike '*run_shared_chrome_agent.py*' -or $oldProcess.CommandLine -notlike '*v2*') { throw 'Reported PID does not identify the v2 listener.' }
if (-not $Execute) { Write-Output 'Exact approval renewal is ready.'; exit 0 }
$backup = Join-Path $reportRoot ('renewal-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $appRoot 'reports\shared_chrome\state') -Destination (Join-Path $backup 'state') -Recurse
Copy-Item -LiteralPath $statusPath -Destination (Join-Path $backup 'previous-status.json')
Stop-Process -Id $oldProcessId
Start-Sleep -Milliseconds 700
if (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) { throw 'Old listener has not stopped.' }
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$process = Start-Process -FilePath (Join-Path $v2Root '.venv\Scripts\python.exe') -ArgumentList @('-u', $entry, '--run', '--renew-approval-run', 'a881c3557b99') -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backup 'runtime.out.log') -RedirectStandardError (Join-Path $backup 'runtime.err.log')
@{started=(Get-Date).ToString('o');previous_pid=$oldProcessId;launcher_pid=$process.Id;version='2026-10-08-approval-delivery';report=$backup;renewed_from='a881c3557b99'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment.json') -Encoding UTF8
Write-Output ('Started approval renewal; launcher PID ' + $process.Id)
