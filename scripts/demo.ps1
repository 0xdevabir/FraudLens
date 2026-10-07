# One-command demo on Windows (Docker Desktop + PowerShell 5.1+ or PowerShell 7+).
# macOS / Linux: use ./scripts/demo.sh or `make demo`.
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is required. Install Docker Desktop for Windows, then retry in a new terminal."
    }
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose v2 is required (the 'docker compose' plugin)."
    }
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is installed but not running. Start Docker Desktop (WSL2 backend recommended), then retry."
    }
}

function Ensure-BackendEnv {
    $envFile = Join-Path $Root "backend\.env"
    if (Test-Path $envFile) { return }

    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $pwBytes = New-Object byte[] 12
        $jwtBytes = New-Object byte[] 32
        $rng.GetBytes($pwBytes)
        $rng.GetBytes($jwtBytes)
    } finally {
        $rng.Dispose()
    }

    $pw = ([BitConverter]::ToString($pwBytes) -replace "-", "").ToLowerInvariant()
    $jwt = ([BitConverter]::ToString($jwtBytes) -replace "-", "").ToLowerInvariant()
    # UTF-8 without BOM so Compose and pydantic both read the file cleanly.
    $text = "FRAUDLENS_SEED_PASSWORD=$pw`nFRAUDLENS_JWT_SECRET=$jwt`n"
    [System.IO.File]::WriteAllText($envFile, $text, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "wrote backend/.env with a generated demo password and signing key"
}

Test-DockerReady
Ensure-BackendEnv

Write-Host "The first start builds the dataset and the models and replays the traffic: about ten minutes."
Write-Host "Then sign in on http://localhost:3100 as analyst1, supervisor1 or admin"
Write-Host "with the password in backend/.env (FRAUDLENS_SEED_PASSWORD)."
Write-Host "Ports 3100, 8010, 5433 and 6380 are bound to 127.0.0.1 only."

docker compose --profile demo up --build @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
