param(
    [switch]$SkipInstall,
    [switch]$SkipDiagnostics,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Model = Join-Path $Root "models\base.en\model.bin"

if (-not (Test-Path $Python)) {
    if ($PythonPath) {
        & $PythonPath -m venv (Join-Path $Root ".venv")
    }
    else {
        try {
            py -3.12 -m venv (Join-Path $Root ".venv")
        }
        catch {
            python -m venv (Join-Path $Root ".venv")
        }
    }
}
if (-not $SkipInstall) {
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
}
if (-not (Test-Path $Model)) {
    throw "The bundled base.en speech model is missing. Repair the Manuscript source package."
}
$env:PYTHONPATH = Join-Path $Root "src"
if (-not $SkipDiagnostics) {
    & $Python (Join-Path $Root "run_echo_link.py") --diagnostics
}
