$ErrorActionPreference = 'Stop'
$appRoot = Join-Path $PSScriptRoot 'course_agent_app'
$reportRoot = Join-Path $appRoot 'reports\blocked_repair_20261009'
$stateRoot = Join-Path $appRoot 'reports\shared_chrome\state'
$tests = Get-Content -LiteralPath (Join-Path $reportRoot 'regression.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $tests.passed) { throw 'Regression tests did not pass.' }
$evaluation = Get-Content -LiteralPath (Join-Path $appRoot 'reports\evaluations\latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($evaluation.passed -ne $evaluation.total -or $evaluation.total -lt 30) { throw 'Repair evaluations did not pass.' }
foreach ($property in $tests.code_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $appRoot $property.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) {
        throw ('Source changed after regression: ' + $property.Name)
    }
}
$status = Get-Content -LiteralPath (Join-Path $appRoot 'reports\shared_chrome\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($status.runtime_version -eq '2026-10-09-blocked-repair') { Write-Output 'Repair listener is already deployed.'; exit 0 }
function Read-RepairBoundary {
    $flow = Get-Content -LiteralPath (Join-Path $stateRoot '29ed78e10116.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($flow.run -ne 'aaef44ae7566' -or $flow.phase -ne 'NEEDS_READBACK' -or $flow.pending_delivery) { throw 'The investigated readback boundary changed; no restart authorized by this script.' }
    foreach ($file in Get-ChildItem -LiteralPath $stateRoot -Filter '*.json') {
        if ($file.BaseName -notmatch '^[a-f0-9]{12}$') { continue }
        $other = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($other.phase -in @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING') -or $other.pending_delivery) { throw 'Another live task or delivery must finish first.' }
    }
    $queue = @(Get-Content -LiteralPath (Join-Path $stateRoot 'inbox.json') -Raw -Encoding UTF8 | ConvertFrom-Json)
    if ($queue.Count -ne 1 -or $queue[0].chat -ne $flow.chat -or $queue[0].sender -ne $flow.sender -or $queue[0].self_mentioned -ne $true) { throw 'The authenticated repair queue changed.' }
    if ($queue[0].id -notmatch '^[A-Za-z0-9_@.:-]+$' -or $queue[0].text -match '^(OK|NO)\s+\d{6}$') { throw 'Only the exact non-approval follow-up may be recovered.' }
    return $queue[0].id
}
$messageId = Read-RepairBoundary
$oldProcessId = [int]$status.pid
$old = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if (-not $old -or $old.CommandLine -notlike '*v2*run_shared_chrome_agent.py*') { throw 'PID is not the v2 listener.' }
$backup = Join-Path $reportRoot ('deployment-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $stateRoot -Destination (Join-Path $backup 'state') -Recurse
if ((Read-RepairBoundary) -ne $messageId) { throw 'Queue changed while backing up; nothing stopped.' }
Stop-Process -Id $oldProcessId
Start-Sleep -Milliseconds 700
if (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) { throw 'Old listener has not exited.' }
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$process = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-u',$entry,'--run','--recover-queued-message',$messageId) -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $reportRoot 'runtime.out.log') -RedirectStandardError (Join-Path $reportRoot 'runtime.err.log')
@{version='2026-10-09-blocked-repair';started=(Get-Date).ToString('o');previous_pid=$oldProcessId;launcher_pid=$process.Id;source_run='aaef44ae7566';recovered_requests=1;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'deployment.json') -Encoding UTF8
Write-Output ('Started repair listener, launcher PID ' + $process.Id + '; preserved task and exact queued instruction.')
