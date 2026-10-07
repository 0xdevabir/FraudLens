#!/bin/sh
# Drop root after fixing volume ownership (Linux named volumes / Podman).
# On Docker Desktop (Mac/Windows) the chown is a no-op when already correct.
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data /app/artifacts
  chown -R fraudlens:fraudlens /app/data /app/artifacts
  exec runuser -u fraudlens -- "$@"
fi

exec "$@"
