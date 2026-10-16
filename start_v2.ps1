param([switch]$Check)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create v2\.venv and install course_agent_app\requirements.txt first.'
}
$entryPath = Join-Path $projectRoot 'course_agent_app\run_shared_chrome_agent.py'
if ($Check) { & $pythonPath $entryPath } else { & $pythonPath $entryPath --run }
exit $LASTEXITCODE
