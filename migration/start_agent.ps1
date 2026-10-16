$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppRoot = Join-Path $Root "course_agent_app"
$Python = Join-Path $AppRoot ".venv\Scripts\python.exe"

$HashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
try {
    $RootHashBytes = $HashAlgorithm.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))
    $RootHash = ([BitConverter]::ToString($RootHashBytes)).Replace("-", "").Substring(0, 16)
} finally {
    $HashAlgorithm.Dispose()
}
$AgentMutex = New-Object System.Threading.Mutex($false, "Local\CourseAgent-$RootHash")
$MutexAcquired = $false
try {
    $MutexAcquired = $AgentMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $MutexAcquired = $true
}
if (-not $MutexAcquired) {
    $AgentMutex.Dispose()
    throw "This course-agent runtime is already running. Stop the existing Agent before starting another copy."
}

if (-not (Test-Path $Python -PathType Leaf)) {
    throw "Missing runtime virtual environment. Run migration\bootstrap_runtime.ps1 first."
}

function First-ExistingFile {
    param([string[]]$Candidates)
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate -PathType Leaf)) {
            return (Resolve-Path $Candidate).Path
        }
    }
    return $null
}

function First-RunnableCodex {
    param([string[]]$Candidates)
    foreach ($Candidate in $Candidates) {
        if (-not $Candidate -or -not (Test-Path $Candidate -PathType Leaf)) {
            continue
        }
        try {
            & $Candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return (Resolve-Path $Candidate).Path
            }
        } catch {
            continue
        }
    }
    return $null
}

$CodexCandidates = @()
if ($env:CODEX_BIN) {
    $CodexCandidates += $env:CODEX_BIN
}
if ($env:LOCALAPPDATA) {
    $CodexUserBin = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
    if (Test-Path $CodexUserBin -PathType Container) {
        $CodexCandidates += Get-ChildItem $CodexUserBin -Recurse -Filter "codex.exe" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -ExpandProperty FullName
    }
    $CodexCandidates += Join-Path $env:LOCALAPPDATA "Programs\ChatGPT\resources\codex.exe"
    $CodexCandidates += Join-Path $env:LOCALAPPDATA "OpenAI\ChatGPT\codex.exe"
}
$CodexCommand = Get-Command codex -ErrorAction SilentlyContinue
if ($null -ne $CodexCommand) {
    $CodexCandidates += $CodexCommand.Source
}
if ($env:PROGRAMFILES) {
    $CodexCandidates += Join-Path $env:PROGRAMFILES "ChatGPT\resources\codex.exe"
}
$env:CODEX_BIN = First-RunnableCodex $CodexCandidates
if (-not $env:CODEX_BIN) {
    throw "No runnable Codex CLI was found. Install Codex CLI or set CODEX_BIN to an executable CLI path."
}

$ChromeCandidates = @()
foreach ($BasePath in @($env:PROGRAMFILES, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if ($BasePath) {
        $ChromeCandidates += Join-Path $BasePath "Google\Chrome\Application\chrome.exe"
    }
}
$Chrome = First-ExistingFile @(
    $env:WHATSAPP_WEB_CHROME_PATH,
    $env:MOODLE_BROWSER_CHROME_PATH
)
if (-not $Chrome) {
    $Chrome = First-ExistingFile $ChromeCandidates
}
if (-not $Chrome) {
    $PlaywrightBrowser = & $Python (Join-Path $Root "migration\resolve_playwright_browser.py")
    if ($LASTEXITCODE -eq 0 -and $PlaywrightBrowser) {
        $Chrome = First-ExistingFile @($PlaywrightBrowser.Trim())
    }
}
if (-not $Chrome) {
    throw "No supported browser was found. Run migration\bootstrap_runtime.ps1 or install Google Chrome."
}

$env:WHATSAPP_WEB_CHROME_PATH = $Chrome
$env:MOODLE_BROWSER_CHROME_PATH = $Chrome

$SofficeCandidates = @()
foreach ($BasePath in @($env:PROGRAMFILES, ${env:ProgramFiles(x86)})) {
    if ($BasePath) {
        $SofficeCandidates += Join-Path $BasePath "LibreOffice\program\soffice.exe"
    }
}
$Soffice = First-ExistingFile $SofficeCandidates
if ($Soffice) {
    $env:SOFFICE = $Soffice
    $env:SOFFICE_BIN = $Soffice
}

$env:WHATSAPP_WEB_PROFILE_DIR = Join-Path $AppRoot ".whatsapp_web_profile"
$env:MOODLE_BROWSER_PROFILE_DIR = Join-Path $AppRoot ".moodle_browser_profile"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$MoodleProxyBypass = @("migrationtest-moodle.hku.hk", "apidev.hku.hk")
$LocalBypass = @("localhost", "127.0.0.1", "::1") + $MoodleProxyBypass
$ExistingBypass = @($env:NO_PROXY, $env:no_proxy) -join ","
$BypassValues = @($ExistingBypass -split "[,;]" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($HostName in $LocalBypass) {
    if ($BypassValues -notcontains $HostName) {
        $BypassValues += $HostName
    }
}
$env:NO_PROXY = $BypassValues -join ","
$env:no_proxy = $env:NO_PROXY
$env:MOODLE_BROWSER_PROXY_BYPASS = if ($env:MOODLE_BROWSER_PROXY_BYPASS) {
    $env:MOODLE_BROWSER_PROXY_BYPASS
} else {
    $MoodleProxyBypass -join ";"
}
$env:COURSE_REFERENCE_PACK_PATH = Join-Path $Root "course_files\reference\DASE7137\2026-2027-sem1"
$env:CODEX_AGENT_NO_RUN = if ($env:CODEX_AGENT_NO_RUN) { $env:CODEX_AGENT_NO_RUN } else { "0" }
$env:CODEX_MOODLE_AGENT_NO_RUN = if ($env:CODEX_MOODLE_AGENT_NO_RUN) { $env:CODEX_MOODLE_AGENT_NO_RUN } else { "0" }
$env:CODEX_LISTENER_FOREVER = if ($env:CODEX_LISTENER_FOREVER) { $env:CODEX_LISTENER_FOREVER } else { "1" }
$env:COURSE_AGENT_FREE_TASK_MODE = if ($env:COURSE_AGENT_FREE_TASK_MODE) { $env:COURSE_AGENT_FREE_TASK_MODE } else { "1" }
$env:COURSE_AGENT_BROWSER_LIFECYCLE = if ($env:COURSE_AGENT_BROWSER_LIFECYCLE) { $env:COURSE_AGENT_BROWSER_LIFECYCLE } else { "agent" }
$env:WHATSAPP_MODE = if ($env:WHATSAPP_MODE) { $env:WHATSAPP_MODE } else { "codex_agent_web" }

& $Python (Join-Path $Root "migration\preflight.py") --strict
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Push-Location $AppRoot
try {
    & $Python "run_course_agent_app.py"
    exit $LASTEXITCODE
} finally {
    Pop-Location
    if ($MutexAcquired) {
        try { $AgentMutex.ReleaseMutex() } catch {}
    }
    $AgentMutex.Dispose()
}
