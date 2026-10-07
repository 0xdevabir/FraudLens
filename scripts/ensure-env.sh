#!/usr/bin/env sh
# Write backend/.env once with a demo password and JWT secret (never committed).
# Works on macOS, Linux, and Git Bash / WSL without requiring Make.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
ENV_FILE="$ROOT/backend/.env"

if [ -f "$ENV_FILE" ]; then
  exit 0
fi

rand_hex() {
  # $1 = number of bytes
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_hex($1))"
  elif command -v python >/dev/null 2>&1; then
    python -c "import secrets; print(secrets.token_hex($1))"
  else
    echo "need openssl or python to generate backend/.env" >&2
    exit 1
  fi
}

umask 077
printf 'FRAUDLENS_SEED_PASSWORD=%s\nFRAUDLENS_JWT_SECRET=%s\n' \
  "$(rand_hex 12)" "$(rand_hex 32)" >"$ENV_FILE"
echo "wrote backend/.env with a generated demo password and signing key"
