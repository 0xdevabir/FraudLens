#!/usr/bin/env sh
# Start Postgres and Redis for host-side development (macOS / Linux / WSL).
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker Desktop, or the docker service, then retry." >&2
  exit 1
fi

exec docker compose up -d --wait "$@"
