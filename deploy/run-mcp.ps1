$ErrorActionPreference = "Stop"

$BackendDirectory = Resolve-Path (Join-Path $PSScriptRoot "..\backend")
$EnvironmentFile = Join-Path $BackendDirectory ".env.mcp"
$Python = Join-Path $BackendDirectory ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
    throw "Missing $EnvironmentFile. Copy .env.mcp.example to .env.mcp and configure it."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing backend virtual environment at $Python"
}

Get-Content -LiteralPath $EnvironmentFile | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
}

Set-Location $BackendDirectory
& $Python -m app.mcp_server
