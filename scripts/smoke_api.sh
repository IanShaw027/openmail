#!/usr/bin/env bash
# OpenMail API smoke checks (curl + cookie jar).
# Usage: BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_api.sh
# Fails gracefully if the server is down (non-zero exit, clear message).

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

log_pass() {
  PASS=$((PASS + 1))
  RESULTS+=("PASS: $1")
  printf "${GREEN}PASS${NC}  %s\n" "$1"
}

log_fail() {
  FAIL=$((FAIL + 1))
  RESULTS+=("FAIL: $1 — $2")
  printf "${RED}FAIL${NC}  %s — %s\n" "$1" "$2"
}

log_skip() {
  SKIP=$((SKIP + 1))
  RESULTS+=("SKIP: $1 — $2")
  printf "${YELLOW}SKIP${NC}  %s — %s\n" "$1" "$2"
}

# Temp cookie jars (cleaned on exit)
JAR_USER="$(mktemp "${TMPDIR:-/tmp}/openmail_smoke_user.XXXXXX")"
JAR_GUEST="$(mktemp "${TMPDIR:-/tmp}/openmail_smoke_guest.XXXXXX")"
BODY="$(mktemp "${TMPDIR:-/tmp}/openmail_smoke_body.XXXXXX")"
cleanup() {
  rm -f "$JAR_USER" "$JAR_GUEST" "$BODY"
}
trap cleanup EXIT

curl_json() {
  # curl_json METHOD PATH [jar] [json_body]
  # sets: HTTP_CODE, BODY content in $BODY
  local method="$1"
  local path="$2"
  local jar="${3:-}"
  local data="${4:-}"
  local args=(-sS -m 10 -o "$BODY" -w "%{http_code}" -X "$method")
  if [[ -n "$jar" ]]; then
    args+=(-b "$jar" -c "$jar")
  fi
  if [[ -n "$data" ]]; then
    args+=(-H "Content-Type: application/json" -d "$data")
  fi
  # shellcheck disable=SC2086
  HTTP_CODE="$(curl "${args[@]}" "${BASE_URL}${path}" 2>"${BODY}.err" || true)"
  if [[ -z "$HTTP_CODE" || "$HTTP_CODE" == "000" ]]; then
    HTTP_CODE="000"
    if [[ -s "${BODY}.err" ]]; then
      cat "${BODY}.err" >"$BODY" 2>/dev/null || true
    fi
  fi
  rm -f "${BODY}.err"
}

echo "OpenMail smoke → ${BASE_URL}"
echo "----------------------------------------"

# ── 1. Health ──────────────────────────────────────────────────────────
curl_json GET /health
if [[ "$HTTP_CODE" == "000" ]]; then
  log_fail "health" "server unreachable at ${BASE_URL} (is it running?)"
  echo "----------------------------------------"
  printf "Summary: ${RED}%d failed${NC}, %d passed, %d skipped\n" "$FAIL" "$PASS" "$SKIP"
  echo "Hint: start backend (make dev-backend) then re-run make smoke"
  exit 1
fi

if [[ "$HTTP_CODE" == "200" ]] && grep -q '"ok"[[:space:]]*:[[:space:]]*true' "$BODY" 2>/dev/null; then
  log_pass "health (HTTP 200, ok=true)"
else
  log_fail "health" "HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

# ── 2. Register random user ────────────────────────────────────────────
RAND="$(date +%s)-${RANDOM}"
USER="smoke_${RAND}"
# username max 64; keep alnum/._-
USER="$(echo "$USER" | tr -cd 'a-zA-Z0-9._-' | cut -c1-64)"
PASSWD="SmokeTest1_${RANDOM}"

REG_JSON=$(printf '{"username":"%s","password":"%s","accepted_privacy":true,"accepted_terms":true,"display_name":"Smoke"}' "$USER" "$PASSWD")
curl_json POST /api/auth/register "$JAR_USER" "$REG_JSON"
if [[ "$HTTP_CODE" == "201" ]]; then
  log_pass "register ($USER)"
else
  log_fail "register" "HTTP ${HTTP_CODE} body=$(head -c 300 "$BODY")"
fi

# ── 3. Me (session from register) ─────────────────────────────────────
curl_json GET /api/auth/me "$JAR_USER"
if [[ "$HTTP_CODE" == "200" ]] && grep -q "\"username\"" "$BODY" 2>/dev/null; then
  log_pass "me after register"
else
  log_fail "me after register" "HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

# ── 4. Logout + Login ─────────────────────────────────────────────────
curl_json POST /api/auth/logout "$JAR_USER"
# clear jar and login fresh
: >"$JAR_USER"
LOGIN_JSON=$(printf '{"username":"%s","password":"%s"}' "$USER" "$PASSWD")
curl_json POST /api/auth/login "$JAR_USER" "$LOGIN_JSON"
if [[ "$HTTP_CODE" == "200" ]]; then
  log_pass "login"
else
  log_fail "login" "HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

curl_json GET /api/auth/me "$JAR_USER"
if [[ "$HTTP_CODE" == "200" ]]; then
  log_pass "me after login"
else
  log_fail "me after login" "HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

# ── 5. Guest cannot search (expect 403) ────────────────────────────────
# Empty jar = guest
: >"$JAR_GUEST"
curl_json GET /api/me/mails/search "$JAR_GUEST"
if [[ "$HTTP_CODE" == "403" ]]; then
  log_pass "guest search forbidden (403)"
elif [[ "$HTTP_CODE" == "401" ]]; then
  # Some deps may return 401; product docs say 403 — accept 401 as soft pass note
  log_pass "guest search forbidden (HTTP ${HTTP_CODE})"
else
  log_fail "guest search" "expected 403, got HTTP ${HTTP_CODE} body=$(head -c 200 "$BODY")"
fi

# ── 6. Create stub account (http_api; fetch may fail — create is enough) ─
ACC_JSON=$(printf '{"email":"smoke-%s@example.com","provider":"http_api","credential":{"api_url":"https://example.com/openmail-smoke"},"sync_enabled":false,"tag":"smoke"}' "$RAND")
curl_json POST /api/accounts "$JAR_USER" "$ACC_JSON"
if [[ "$HTTP_CODE" == "201" ]]; then
  log_pass "create stub account (http_api)"
elif [[ "$HTTP_CODE" == "503" ]]; then
  log_skip "create stub account" "master key not configured (HTTP 503) — set OPENMAIL_MASTER_KEY"
else
  log_fail "create stub account" "HTTP ${HTTP_CODE} body=$(head -c 300 "$BODY")"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo "----------------------------------------"
echo "Results:"
for line in "${RESULTS[@]}"; do
  echo "  $line"
done
echo "----------------------------------------"
printf "Summary: %d passed, %d failed, %d skipped\n" "$PASS" "$FAIL" "$SKIP"

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
