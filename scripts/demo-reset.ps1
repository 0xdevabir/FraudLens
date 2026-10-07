# Stop the demo and delete its database, dataset and models (Windows).
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

docker compose --profile demo down --volumes @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
