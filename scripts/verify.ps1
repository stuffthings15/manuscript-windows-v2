param(
    [switch]$WriteCurrent
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $WorkspacePython = Join-Path (Split-Path -Parent (Split-Path -Parent $Root)) ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $WorkspacePython) {
        $Python = $WorkspacePython
    }
}
$VerifyBase = [System.IO.Path]::GetFullPath(
    (Join-Path ([System.IO.Path]::GetTempPath()) "ManuscriptVerify")
)
$VerifyRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $VerifyBase ("verify-" + [guid]::NewGuid().ToString("N")))
)
$ManagedEnvironment = @(
    "PYTHONPATH",
    "MANUSCRIPT_V2_DATA_DIR",
    "ECHOLINK_DISABLE_HOTKEY",
    "ECHOLINK_DISABLE_SOUNDS",
    "ECHOLINK_DISABLE_MEDIA_CONTROL",
    "ECHOLINK_DISABLE_AUTO_HUD",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "QT_QPA_PLATFORM"
)
$PreviousEnvironment = @{}
foreach ($Name in $ManagedEnvironment) {
    $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing Manuscript or Echo-Link .venv. Run scripts\bootstrap.ps1 first."
}

New-Item -ItemType Directory -Path (Join-Path $VerifyRoot "data") -Force | Out-Null
$env:PYTHONPATH = Join-Path $Root "src"
$env:MANUSCRIPT_V2_DATA_DIR = Join-Path $VerifyRoot "data"
$env:QT_QPA_PLATFORM = "offscreen"
$env:ECHOLINK_DISABLE_HOTKEY = "1"
$env:ECHOLINK_DISABLE_SOUNDS = "1"
$env:ECHOLINK_DISABLE_MEDIA_CONTROL = "1"
$env:ECHOLINK_DISABLE_AUTO_HUD = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

try {

$PytestBase = Join-Path $VerifyRoot "pytest"
New-Item -ItemType Directory -Path $PytestBase -Force | Out-Null
& $Python -m pytest -q --basetemp $PytestBase -p no:cacheprovider
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed."
}

$evaluation = @(
    (Join-Path $Root "tools\evaluate_capstone.py"),
    "--check"
)
if (-not $WriteCurrent) {
    $evaluation += "--no-write"
}
& $Python @evaluation
if ($LASTEXITCODE -ne 0) {
    throw "Capstone evaluation failed."
}

& $Python (Join-Path $Root "tools\privacy_check.py")
if ($LASTEXITCODE -ne 0) {
    throw "Privacy check failed."
}

& $Python (Join-Path $Root "tools\evaluate_writing_quality.py") --reference-self-check --check | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Writing-quality corpus self-check failed."
}

& $Python (Join-Path $Root "tools\diagnostics_check.py")
if ($LASTEXITCODE -ne 0) {
    throw "Diagnostics check failed."
}

& $Python (Join-Path $Root "tools\recovery_crash_probe.py")
if ($LASTEXITCODE -ne 0) {
    throw "Crash recovery probe failed."
}

& $Python (Join-Path $Root "tools\reliability_fault_probe.py")
if ($LASTEXITCODE -ne 0) {
    throw "Reliability fault probe failed."
}

& $Python (Join-Path $Root "tools\build.py") --dry-run --installer
if ($LASTEXITCODE -ne 0) {
    throw "Build dry-run failed."
}

Write-Host "Manuscript verification passed."
}
finally {
    foreach ($Name in $ManagedEnvironment) {
        [Environment]::SetEnvironmentVariable(
            $Name,
            $PreviousEnvironment[$Name],
            "Process"
        )
    }
    $ExpectedPrefix = $VerifyBase + [System.IO.Path]::DirectorySeparatorChar
    if (-not $VerifyRoot.StartsWith($ExpectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean verification data outside .test-data."
    }
    if (Test-Path -LiteralPath $VerifyRoot) {
        Remove-Item -LiteralPath $VerifyRoot -Recurse -Force
    }
}
