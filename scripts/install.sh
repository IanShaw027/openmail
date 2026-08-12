#!/usr/bin/env bash
# Bootstrap OpenMail with Docker Compose (local-first).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> OpenMail install"
echo "    repo: $ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "error: docker compose plugin not found" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "==> created .env from .env.example"
  else
    echo "error: missing .env.example" >&2
    exit 1
  fi
fi

if ! grep -qE '^OPENMAIL_MASTER_KEY=.+' .env 2>/dev/null \
  || grep -qE '^OPENMAIL_MASTER_KEY=\s*$' .env 2>/dev/null \
  || grep -qE '^OPENMAIL_MASTER_KEY=change' .env 2>/dev/null; then
  KEY="$(python3 -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())' 2>/dev/null \
    || openssl rand -base64 32)"
  if grep -q '^OPENMAIL_MASTER_KEY=' .env; then
    # portable-ish replace
    if [[ "$(uname -s)" == "Darwin" ]]; then
      sed -i '' "s|^OPENMAIL_MASTER_KEY=.*|OPENMAIL_MASTER_KEY=${KEY}|" .env
    else
      sed -i "s|^OPENMAIL_MASTER_KEY=.*|OPENMAIL_MASTER_KEY=${KEY}|" .env
    fi
  else
    printf '\nOPENMAIL_MASTER_KEY=%s\n' "$KEY" >> .env
  fi
  echo "==> generated OPENMAIL_MASTER_KEY"
else
  echo "==> OPENMAIL_MASTER_KEY already set"
fi

# The container runs as a non-root user and ./data is a bind mount, so the host
# directory has to be writable by that uid. Left to Docker, a missing ./data is
# created as root:root and the very first start fails to create the database.
# (macOS is immune because Docker Desktop maps uids, so this only bites Linux.)
mkdir -p data
if [[ "$(uname -s)" == "Linux" ]]; then
  host_uid="$(id -u)"
  host_gid="$(id -g)"
  if [[ "$host_uid" == "0" ]]; then
    # Installing as root: hand the directory to the image user rather than
    # running the container as root just to make the mount writable.
    chown -R 10001:10001 data 2>/dev/null \
      && echo "==> data/ owned by 10001:10001 (container user)" \
      || echo "warn: could not chown data/ — container may fail to write /data" >&2
  else
    # Otherwise run the container as the invoking user, who already owns ./data.
    grep -q '^OPENMAIL_UID=' .env || printf 'OPENMAIL_UID=%s\n' "$host_uid" >> .env
    grep -q '^OPENMAIL_GID=' .env || printf 'OPENMAIL_GID=%s\n' "$host_gid" >> .env
    echo "==> container will run as ${host_uid}:${host_gid} to match ./data"
  fi
fi

# Export so `pull` and `up` cannot disagree about which image they act on.
export OPENMAIL_IMAGE="${OPENMAIL_IMAGE:-ianshaw027/openmail:v0.3.6}"
echo "==> image: $OPENMAIL_IMAGE"
# Prefer pull of published image; fall back to local build if pull fails (offline / private).
if docker compose pull openmail; then
  echo "==> docker compose up -d (pulled)"
  docker compose up -d
else
  echo "==> pull failed (see above) — building locally"
  docker compose up -d --build
fi

echo
echo "Done."
echo "  UI/API:  http://127.0.0.1:8000"
echo "  Health:  curl -s http://127.0.0.1:8000/api/health"
echo "  Image:   $OPENMAIL_IMAGE  (Docker Hub / override OPENMAIL_IMAGE)"
echo "  Demo:    https://mail.clomio.ai"
echo
echo "First visit: create vault password → save recovery key → import accounts."
