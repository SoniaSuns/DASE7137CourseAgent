param([switch]$SetupOnly, [switch]$Check)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$entryPath = Join-Path $PSScriptRoot 'course_agent_app\dedicated_chrome.py'
$agentArguments = @($entryPath)
if ($SetupOnly) { $agentArguments += '--setup-only' }
if ($Check) { $agentArguments += '--check' }
& $pythonPath @agentArguments
exit $LASTEXITCODE
