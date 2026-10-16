$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot 'course_agent_app')
try {
    & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') run_evaluations.py
    if ($LASTEXITCODE -ne 0) { throw 'Evaluation failed. See reports/evaluations/latest.json.' }
    & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') build_diagnostic_report.py
    if ($LASTEXITCODE -ne 0) { throw 'Report generation failed.' }
} finally { Pop-Location }
