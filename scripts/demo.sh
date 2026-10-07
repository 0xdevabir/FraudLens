#!/usr/bin/env sh
# One-command demo on macOS, Linux, and Git Bash / WSL.
# Windows PowerShell: use scripts/demo.ps1 instead.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop (Mac/Windows) or Docker Engine (Linux)." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required (the 'docker compose' plugin)." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker Desktop, or the docker service, then retry." >&2
  exit 1
fi

sh "$ROOT/scripts/ensure-env.sh"

echo "The first start builds the dataset and the models and replays the traffic: about ten minutes."
echo "Then sign in on http://localhost:3100 as analyst1, supervisor1 or admin"
echo "with the password in backend/.env (FRAUDLENS_SEED_PASSWORD)."
echo "Ports 3100, 8010, 5433 and 6380 are bound to 127.0.0.1 only."

exec docker compose --profile demo up --build "$@"
