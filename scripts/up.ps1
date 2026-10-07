# Start Postgres and Redis for host-side development (Windows Docker Desktop).
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but not running. Start Docker Desktop, then retry."
}

docker compose up -d --wait @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
