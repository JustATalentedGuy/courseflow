param(
    [Parameter(Mandatory = $true)]
    [string]$IpAddress,
    [string]$EnvironmentFile = (Join-Path $PSScriptRoot "..\.env.deploy")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
    throw "Missing $EnvironmentFile. Copy .env.deploy.example to .env.deploy and configure it."
}

$values = @{}
Get-Content -LiteralPath $EnvironmentFile | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        $values[$matches[1].Trim()] = $matches[2].Trim()
    }
}
$subdomain = $values["DUCKDNS_SUBDOMAIN"]
$token = $values["DUCKDNS_TOKEN"]
if (-not $subdomain -or -not $token) {
    throw "DUCKDNS_SUBDOMAIN and DUCKDNS_TOKEN are required in $EnvironmentFile."
}

$query = "https://www.duckdns.org/update?domains=$([uri]::EscapeDataString($subdomain))" +
    "&token=$([uri]::EscapeDataString($token))&ip=$([uri]::EscapeDataString($IpAddress))"
$result = (Invoke-RestMethod -Uri $query -Method Get).Trim()
if ($result -ne "OK") {
    throw "DuckDNS update failed."
}
Write-Host "DuckDNS updated: $subdomain.duckdns.org -> $IpAddress"
