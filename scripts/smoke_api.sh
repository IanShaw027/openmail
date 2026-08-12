#!/usr/bin/env bash
# OpenMail API smoke checks for the local-first / vault-device model.
#   BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_api.sh
#
# Flow: health → public config → device register → HMAC-signed GET /api/accounts
#       → unsigned GET /api/accounts must be rejected (401).
# There is no user registration / login (that system was removed).
#
# Requires: curl, openssl, od (all standard). Fails gracefully if server is down.

set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"

PASS=0
FAIL=0
SKIP=0
RESULTS=()

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_pass() { PASS=$((PASS + 1)); RESULTS+=("PASS: $1"); printf "${GREEN}PASS${NC}  %s\n" "$1"; }
log_fail() { FAIL=$((FAIL + 1)); RESULTS+=("FAIL: $1 — $2"); printf "${RED}FAIL${NC}  %s — %s\n" "$1" "$2"; }
log_skip() { SKIP=$((SKIP + 1)); RESULTS+=("SKIP: $1 — $2"); printf "${YELLOW}SKIP${NC}  %s — %s\n" "$1" "$2"; }

BODY="$(mktemp "${TMPDIR:-/tmp}/openmail_smoke_body.XXXXXX")"
cleanup() { rm -f "$BODY" "${BODY}.err"; }
trap cleanup EXIT

# curl_code METHOD PATH [header...] — sets HTTP_CODE, writes response to $BODY
curl_code() {
  local method="$1" path="$2"; shift 2
  local args=(-sS -m 10 -o "$BODY" -w "%{http_code}" -X "$method")
  local h
  for h in "$@"; do args+=(-H "$h"); done
  HTTP_CODE="$(curl "${args[@]}" "${BASE_URL}${path}" 2>"${BODY}.err" || true)"
  if [[ -z "$HTTP_CODE" || "$HTTP_CODE" == "000" ]]; then
    HTTP_CODE="000"
    [[ -s "${BODY}.err" ]] && cat "${BODY}.err" >"$BODY" 2>/dev/null || true
  fi
}

echo "OpenMail smoke → ${BASE_URL}"
echo "----------------------------------------"

if ! command -v openssl >/dev/null 2>&1 || ! command -v od >/dev/null 2>&1; then
  echo "Missing openssl/od — cannot sign device requests."
  exit 1
fi

# ── 1. Health ──────────────────────────────────────────────────────────
curl_code GET /api/health
if [[ "$HTTP_CODE" == "000" ]]; then
  log_fail "health" "server unreachable at ${BASE_URL} (is it running?)"
  echo "----------------------------------------"
  printf "Summary: ${RED}%d failed${NC}, %d passed, %d skipped\n" "$FAIL" "$PASS" "$SKIP"
  echo "Hint: start backend (make dev-backend) then re-run make smoke"
  exit 1
fi
if [[ "$HTTP_CODE" == "200" ]] && grep -q '"ok"[[:space:]]*:[[:space:]]*true' "$BODY"; then
  log_pass "health (HTTP 200, ok=true)"
else
  log_fail "health" "HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

# ── 2. Public config (no auth) ─────────────────────────────────────────
curl_code GET /api/config/public
if [[ "$HTTP_CODE" == "200" ]]; then
  log_pass "public config (HTTP 200)"
else
  log_fail "public config" "HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

# ── 3. Register a throwaway vault device ───────────────────────────────
# Secret: 32 random bytes. Canonical id = vk_<sha256(raw_secret)[:40]>.
SECRET_B64="$(openssl rand -base64 32)"
RAW_HEX="$(printf '%s' "$SECRET_B64" | openssl base64 -d -A | od -An -v -tx1 | tr -d ' \n')"
SHA="$(printf '%s' "$SECRET_B64" | openssl base64 -d -A | openssl dgst -sha256 | awk '{print $NF}')"
PUBLIC_ID="vk_${SHA:0:40}"

REG_JSON="$(printf '{"public_id":"%s","secret_b64":"%s"}' "$PUBLIC_ID" "$SECRET_B64")"
HTTP_CODE="$(curl -sS -m 10 -o "$BODY" -w "%{http_code}" -X POST \
  -H "Content-Type: application/json" -d "$REG_JSON" \
  "${BASE_URL}/api/device/register" 2>/dev/null || echo 000)"
if [[ "$HTTP_CODE" == "200" ]]; then
  log_pass "device register (${PUBLIC_ID})"
elif [[ "$HTTP_CODE" == "503" ]]; then
  log_skip "device register" "master key not configured (HTTP 503) — set OPENMAIL_MASTER_KEY"
else
  log_fail "device register" "HTTP ${HTTP_CODE} body=$(head -c 300 "$BODY")"
fi

# ── 4. HMAC-signed GET /api/accounts (path-only form is valid for GET) ──
# Signed message: {ts}.{METHOD}.{path}
if [[ "$PUBLIC_ID" == vk_* ]]; then
  TS="$(date +%s)"
  ACC_PATH="/api/accounts"
  MSG="${TS}.GET.${ACC_PATH}"
  SIG="$(printf '%s' "$MSG" | openssl dgst -sha256 -mac HMAC -macopt "hexkey:${RAW_HEX}" | awk '{print $NF}')"

  curl_code GET "$ACC_PATH" \
    "X-Device-Id: ${PUBLIC_ID}" \
    "X-Device-Ts: ${TS}" \
    "X-Device-Sign: ${SIG}"
  if [[ "$HTTP_CODE" == "200" ]]; then
    log_pass "signed GET /api/accounts (HTTP 200)"
  else
    log_fail "signed GET /api/accounts" "HTTP ${HTTP_CODE} body=$(head -c 300 "$BODY")"
  fi

  # ── 5. Unsigned request must be rejected ─────────────────────────────
  curl_code GET "$ACC_PATH"
  if [[ "$HTTP_CODE" == "401" ]]; then
    log_pass "unsigned GET /api/accounts rejected (401)"
  else
    log_fail "unsigned GET /api/accounts" "expected 401, got HTTP ${HTTP_CODE}"
  fi
fi

# ── Summary ────────────────────────────────────────────────────────────
echo "----------------------------------------"
echo "Results:"
for line in "${RESULTS[@]}"; do echo "  $line"; done
echo "----------------------------------------"
printf "Summary: %d passed, %d failed, %d skipped\n" "$PASS" "$FAIL" "$SKIP"

[[ "$FAIL" -gt 0 ]] && exit 1
exit 0
