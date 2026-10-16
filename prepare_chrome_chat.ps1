param()
$ErrorActionPreference = 'Stop'
$panelPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $panelPython (Join-Path $PSScriptRoot 'course_agent_app\prepare_chrome_chat.py')
if ($LASTEXITCODE -ne 0) { throw 'Chrome chat extension preparation failed.' }
