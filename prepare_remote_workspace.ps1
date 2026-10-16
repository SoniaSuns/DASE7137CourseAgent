$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'course_agent_app\remote_workspace_admin.py') prepare
exit $LASTEXITCODE
