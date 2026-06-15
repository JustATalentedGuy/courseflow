$ErrorActionPreference = "Stop"

$BackendDirectory = Resolve-Path (Join-Path $PSScriptRoot "..\backend")
$Python = Join-Path $BackendDirectory ".venv\Scripts\python.exe"
$Launcher = Join-Path $PSScriptRoot "run_mcp.py"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing backend virtual environment at $Python"
}

& $Python $Launcher
