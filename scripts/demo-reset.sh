#!/usr/bin/env sh
# Stop the demo and delete its database, dataset and models.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
exec docker compose --profile demo down --volumes "$@"
