# Generic v2 listener deployment. Deploys the committed git HEAD only.
#   .\deploy_v2.ps1                       checks only (clean tree, full test suite, idle listener)
#   .\deploy_v2.ps1 -Execute              also restarts the listener when idle
#   .\deploy_v2.ps1 -Execute -WaitForIdle waits for active tasks to settle first
# Restarting interrupts nothing in flight: it refuses while any task, queued message,
# unconfirmed delivery or possible save is active. Pending approvals would be invalidated
# by a restart, so APPROVAL also counts as active.
param([switch]$Execute,[switch]$WaitForIdle,[string]$RecoverQuestionRun,[string]$RetryFailedRun,[string]$ReplanWaitingRun,[switch]$RecoverMentioned)
$ErrorActionPreference = 'Stop'
$recoveryModes = @(@($RecoverQuestionRun,$RetryFailedRun,$ReplanWaitingRun) | Where-Object { $_ })
if ($recoveryModes.Count -gt 1 -or @($recoveryModes | Where-Object { $_ -notmatch '^[a-f0-9]{12}$' }).Count -gt 0) {
    throw 'Choose one recovery mode with one exact run ID.'
}
if ($RecoverMentioned -and $recoveryModes.Count) { throw 'Choose only one recovery mode.' }
$v2Root = [IO.Path]::GetFullPath($PSScriptRoot)
$appRoot = Join-Path $v2Root 'course_agent_app'
if ($RecoverMentioned -and -not (Test-Path -LiteralPath (Join-Path $appRoot 'reports\shared_chrome\missed_mention_candidate.json'))) {
    throw 'Inspect and record the exact missed mention before requesting recovery.'
}
$pythonPath = Join-Path $v2Root '.venv\Scripts\python.exe'
$statusPath = Join-Path $appRoot 'reports\shared_chrome\status.json'
$stateRoot = Join-Path $appRoot 'reports\shared_chrome\state'
$active = @('PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK')

function Invoke-Git { & git -c ('safe.directory=' + ($v2Root -replace '\\','/')) -C $v2Root @args }
function Get-CleanCommit {
    $dirty = Invoke-Git status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'git status failed.' }
    if ($dirty) { throw "Uncommitted changes exist. Commit them first so the deployed code is recorded:`n$dirty" }
    return (Invoke-Git rev-parse HEAD).Trim()
}
function Test-RemoteWorkspaceBusy {
    # Heartbeat may lag an incoming remote request. Check the durable store too.
    & $pythonPath (Join-Path $appRoot 'remote_workspace_admin.py') check-idle
    if ($LASTEXITCODE -eq 2) { return $true }
    if ($LASTEXITCODE -ne 0) { throw 'Remote workspace idle check failed.' }
    return $false
}

function Test-ListenerBusy {
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($status.queued -ne 0 -or @($status.pending_delivery).Count -gt 0) { return $true }
    if ($status.remote_workspace -and $status.remote_workspace.active_tasks -gt 0) { return $true }
    foreach ($property in $status.phases.PSObject.Properties) { if ($active -contains $property.Value) { return $true } }
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

function Test-DeploymentBlocked {
    # Owner-task recovery flags must never interrupt another user's remote task.
    if (Test-RemoteWorkspaceBusy) { return $true }
    if ($ReplanWaitingRun) {
        # Explicit operator recovery only: no active work, approval or possible save.
        & $pythonPath (Join-Path $appRoot 'question_recovery.py') --waiting --run $ReplanWaitingRun | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Delivered-question replan preconditions failed; no process stopped.' }
        return $false
    }
    if (-not $RecoverQuestionRun) {
        if (Test-ListenerBusy) { return $true }
        if ($RetryFailedRun) {
            & $pythonPath (Join-Path $appRoot 'question_recovery.py') --failed --run $RetryFailedRun | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Failed-run retry preconditions failed; no process stopped.' }
        }
        return $false
    }
    # This opt-in exception requires explicit owner authorization for a controlled
    # restart. Defaults still refuse all active tasks and pending deliveries.
    & $pythonPath (Join-Path $appRoot 'question_recovery.py') --run $RecoverQuestionRun | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Question recovery preconditions failed; no process stopped.' }
    return $false
}

$commit = Get-CleanCommit
Push-Location $appRoot
try {
    & $pythonPath -m unittest discover -p 'test_*.py' -q
    if ($LASTEXITCODE -ne 0) { throw 'The full test suite must pass before deployment.' }
} finally { Pop-Location }

while (Test-DeploymentBlocked) {
    if (-not $WaitForIdle) { throw 'A task, queued message or delivery is active. Use -WaitForIdle or retry later.' }
    Start-Sleep -Seconds 15
}
if (-not $Execute) { Write-Output ('Ready to deploy commit ' + $commit + '. Re-run with -Execute to restart the listener.'); exit 0 }

$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
$oldProcessId = [int]$status.pid
$oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldProcessId"
if ($oldProcess -and ($oldProcess.CommandLine -notlike '*run_shared_chrome_agent.py*' -or $oldProcess.CommandLine -notlike '*v2*')) {
    throw 'Reported PID is not the v2 listener.'
}
# Runtime state is data, not source: back it up so a bad release can be inspected or restored.
$backup = Join-Path $appRoot ('reports\deployments\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $commit.Substring(0,8))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $stateRoot -Destination (Join-Path $backup 'state') -Recurse
Copy-Item -LiteralPath $statusPath -Destination (Join-Path $backup 'previous-status.json')
# Recheck immediately before stopping.
if ((Get-CleanCommit) -ne $commit) { throw 'Source changed during checks; no process stopped.' }
if (Test-DeploymentBlocked) { throw 'A task became active; no process stopped.' }
if ($oldProcess) {
    Stop-Process -Id $oldProcessId
    Start-Sleep -Milliseconds 700
    if (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) { throw 'Old listener has not stopped.' }
}
$env:COURSE_AGENT_SOURCE_COMMIT = $commit
$entry = Join-Path $appRoot 'run_shared_chrome_agent.py'
$listenerArguments = @('-u', $entry, '--run')
if ($RecoverQuestionRun) { $listenerArguments += @('--recover-question-run', $RecoverQuestionRun) }
if ($RetryFailedRun) { $listenerArguments += @('--retry-failed-run', $RetryFailedRun) }
if ($ReplanWaitingRun) { $listenerArguments += @('--replan-waiting-run', $ReplanWaitingRun) }
if ($RecoverMentioned) { $listenerArguments += '--recover-mentioned' }
$process = Start-Process -FilePath $pythonPath -ArgumentList $listenerArguments -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $backup 'runtime.out.log') -RedirectStandardError (Join-Path $backup 'runtime.err.log')
@{started=(Get-Date).ToString('o');commit=$commit;previous_pid=$oldProcessId;launcher_pid=$process.Id;state_backup=$backup} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup 'deployment.json') -Encoding UTF8
Write-Output ('Started v2 listener at commit ' + $commit + '; launcher PID ' + $process.Id)
