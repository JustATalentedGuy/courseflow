$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$EnvFile = Join-Path $Root ".env.edge-fetcher"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.edge-fetcher.example") $EnvFile
    Write-Host "Created $EnvFile. Add COURSEFLOW_EDGE_TOKEN, then rerun this script."
    exit 1
}

& $Python -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to install local fetcher requirements."
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Root\edge_fetcher.py`" --env-file `"$EnvFile`"" `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask `
    -TaskName "CourseFlow Local Transcript Fetcher" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Fetches YouTube metadata, captions, and fallback audio from the local residential IP for CourseFlow." `
    -Force | Out-Null

Start-ScheduledTask -TaskName "CourseFlow Local Transcript Fetcher"
Write-Host "Installed and started CourseFlow Local Transcript Fetcher."
