$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppRoot = Join-Path $Root "course_agent_app"
$VenvRoot = Join-Path $AppRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$BundledPythonInstaller = Join-Path $Root "python-3.11.9-amd64.exe"

function Test-Python311 {
    param(
        [string]$Command,
        [string[]]$Prefix = @()
    )
    if (-not $Command -or -not (Test-Path $Command -PathType Leaf)) {
        return $false
    }
    try {
        & $Command @Prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        if (Test-Python311 $py.Source @("-3.11")) {
            return @{
                Command = $py.Source
                Prefix = @("-3.11")
            }
        }
        if (Test-Python311 $py.Source @("-3")) {
            return @{
                Command = $py.Source
                Prefix = @("-3")
            }
        }
    }
    if ($env:LOCALAPPDATA) {
        $LocalPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
        if (Test-Python311 $LocalPython) {
            return @{
                Command = $LocalPython
                Prefix = @()
            }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python -and (Test-Python311 $python.Source)) {
        return @{
            Command = $python.Source
            Prefix = @()
        }
    }
    return $null
}

$Python = Resolve-PythonCommand
if ($null -eq $Python -and (Test-Path $BundledPythonInstaller -PathType Leaf)) {
    Write-Host "Python 3.11+ was not found. Installing the bundled Python 3.11 runtime for the current user..."
    $Installer = Start-Process -FilePath $BundledPythonInstaller -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_launcher=1",
        "Include_pip=1",
        "Include_test=0",
        "Shortcuts=0"
    ) -WindowStyle Hidden -Wait -PassThru
    if ($Installer.ExitCode -ne 0) {
        throw "The bundled Python installer exited with code $($Installer.ExitCode)."
    }
    $Python = Resolve-PythonCommand
}
if ($null -eq $Python) {
    throw "Python 3.11 or newer was not found, and no usable bundled installer was available."
}
$PythonCommand = $Python.Command
$PythonPrefix = $Python.Prefix
& $PythonCommand @PythonPrefix --version
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $PythonCommand @PythonPrefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required."
}

if (Test-Path $VenvPython -PathType Leaf) {
    & $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        $BrokenVenv = "$VenvRoot.broken-$((Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'))"
        Move-Item -LiteralPath $VenvRoot -Destination $BrokenVenv
        Write-Warning "The existing virtual environment was unusable and was preserved at $BrokenVenv"
    }
}

if (-not (Test-Path $VenvPython -PathType Leaf)) {
    & $PythonCommand @PythonPrefix -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $VenvPython -m pip install -r (Join-Path $AppRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $VenvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$EnvPath = Join-Path $AppRoot ".env"
if (-not (Test-Path $EnvPath -PathType Leaf)) {
    Copy-Item (Join-Path $AppRoot ".env.example") $EnvPath
    Write-Host "Created $EnvPath from the template."
}

$Directories = @(
    (Join-Path $AppRoot ".whatsapp_web_profile"),
    (Join-Path $AppRoot ".moodle_browser_profile"),
    (Join-Path $AppRoot "reports"),
    (Join-Path $Root "course_files\evidence\process\course_agent_app\whatsapp_prompt_attachments")
)
foreach ($Directory in $Directories) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
}

& $VenvPython (Join-Path $Root "migration\preflight.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Run migration\start_agent.ps1 to start the course agent."
