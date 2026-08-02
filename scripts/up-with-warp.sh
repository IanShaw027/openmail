#!/usr/bin/env bash
# Start OpenMail + 10 Cloudflare WARP SOCKS nodes on the same stack.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data/warp/{1..10}
if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example and set OPENMAIL_MASTER_KEY / ADMIN_PASSWORD"
  exit 1
fi
# Ensure tun exists
if [[ ! -e /dev/net/tun ]]; then
  echo "WARNING: /dev/net/tun missing — WARP nodes may fail to start"
fi
export FETCH_CONCURRENCY="${FETCH_CONCURRENCY:-10}"
export SYNC_CONCURRENCY="${SYNC_CONCURRENCY:-10}"
export PROXY_SID_STRATEGY="${PROXY_SID_STRATEGY:-sticky_per_account}"
# Only enable pool when WARP profile is up (empty by default in compose)
export PROXY_POOL="${PROXY_POOL:-socks5://warp-1:1080|socks5://warp-2:1080|socks5://warp-3:1080|socks5://warp-4:1080|socks5://warp-5:1080|socks5://warp-6:1080|socks5://warp-7:1080|socks5://warp-8:1080|socks5://warp-9:1080|socks5://warp-10:1080}"
docker compose --profile warp up -d --build "$@"
# Recreate openmail so it picks PROXY_POOL from this shell
docker compose --profile warp up -d --force-recreate --no-deps openmail
echo ""
echo "OpenMail: http://127.0.0.1:8000"
echo "PROXY_POOL set to 10x socks5://warp-N:1080"
echo "WARP nodes: warp-1..10 on Docker network openmail-net"
echo "Docs: docs/16-warp-proxy-pool.md"
docker compose --profile warp ps
