param([switch]$Json,[switch]$Follow,[switch]$Metrics)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($Metrics) {
    # Task-level production outcomes: first-run success and runs per task.
    $metricsArguments = @('-u', (Join-Path $PSScriptRoot 'course_agent_app\task_metrics.py'))
    if ($Json) { $metricsArguments += '--json' }
    & $pythonPath @metricsArguments
    exit $LASTEXITCODE
}
$entry = Join-Path $PSScriptRoot 'course_agent_app\agent_status.py'
$statusArguments = @('-u', $entry)
if ($Json) { $statusArguments += '--json' }
if ($Follow) { $statusArguments += '--follow' }
& $pythonPath @statusArguments
exit $LASTEXITCODE
