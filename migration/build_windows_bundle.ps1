[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $Root "migration_output"
}
$SourceManifest = Join-Path $Root "MIGRATION_MANIFEST.json"
if (-not (Test-Path $SourceManifest -PathType Leaf)) {
    throw "MIGRATION_MANIFEST.json is required."
}

$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$BundleName = "course-agent-runtime-windows-$Timestamp"
$StageRoot = Join-Path $OutputDirectory $BundleName
$ZipPath = Join-Path $OutputDirectory "$BundleName.zip"
$ChecksumPath = "$ZipPath.sha256"

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
if (Test-Path $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null

$Manifest = Get-Content -Raw $SourceManifest | ConvertFrom-Json
$RelativeFiles = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($Entry in $Manifest.files) {
    [void]$RelativeFiles.Add([string]$Entry.path)
}

# Keep only reference inputs named by the current provenance manifest. The
# baseline migration manifest can point at sources from an older rebuild.
$ReferenceSourcePrefix = "course_files/reference/DASE7137/2026-2027-sem1/sources/"
foreach ($OldSource in @($RelativeFiles | Where-Object {
    $_.Replace("\", "/").StartsWith($ReferenceSourcePrefix, [StringComparison]::OrdinalIgnoreCase)
})) {
    [void]$RelativeFiles.Remove($OldSource)
}
$CurrentSourceManifestPath = Join-Path $Root "course_files\reference\DASE7137\2026-2027-sem1\source_manifest.json"
$CurrentSourceManifest = Get-Content -Raw $CurrentSourceManifestPath | ConvertFrom-Json
foreach ($SourceFile in $CurrentSourceManifest.source_files) {
    [void]$RelativeFiles.Add(([string]$SourceFile.path).Replace("\", "/"))
}
foreach ($Extra in @(
    "docs/MIGRATION_TEST_ISSUES.md",
    "migration/build_windows_bundle.ps1",
    "migration/resolve_playwright_browser.py",
    "course_agent_app/windows_office_pdf_worker.py",
    "python-3.11.9-amd64.exe"
)) {
    if (Test-Path (Join-Path $Root $Extra) -PathType Leaf) {
        [void]$RelativeFiles.Add($Extra)
    }
}

$FileRecords = @()
foreach ($Relative in ($RelativeFiles | Sort-Object)) {
    $Source = Join-Path $Root $Relative
    if (-not (Test-Path $Source -PathType Leaf)) {
        throw "Manifest source is missing: $Relative"
    }
    $Destination = Join-Path $StageRoot $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
    $Item = Get-Item -LiteralPath $Destination
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    $FileRecords += [ordered]@{ path = $Relative.Replace("\", "/"); bytes = $Item.Length; sha256 = $Hash }
}

$NewManifest = [ordered]@{
    format = "course-agent-runtime-bundle/v2"
    built_at = (Get-Date).ToUniversalTime().ToString("o")
    target_platform = "windows"
    entrypoint = "course_agent_app/run_course_agent_app.py"
    file_count = $FileRecords.Count
    files = $FileRecords
    credential_bundle = $Manifest.credential_bundle
    excluded_state = $Manifest.excluded_state
    destination_steps = @(
        "Set-ExecutionPolicy -Scope Process Bypass",
        ".\migration\bootstrap_runtime.ps1",
        ".\migration\start_agent.ps1"
    )
}
[IO.File]::WriteAllText(
    (Join-Path $StageRoot "MIGRATION_MANIFEST.json"),
    ($NewManifest | ConvertTo-Json -Depth 8),
    $Utf8NoBom
)

Compress-Archive -LiteralPath $StageRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$ZipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
"$ZipHash  $([IO.Path]::GetFileName($ZipPath))" | Set-Content -Encoding ASCII $ChecksumPath

Remove-Item -LiteralPath $StageRoot -Recurse -Force
Write-Output $ZipPath
Write-Output $ChecksumPath
