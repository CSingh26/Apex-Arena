#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Post-deployment smoke test for the public Apex Arena surface.
#
# Runs read-only requests against the PUBLIC domain to prove the proxy chain
# works end to end and that no internal origin leaks to users.
#
# Usage:
#   PUBLIC_BASE_URL=https://chaitanyasingh.org/apex-arena \
#   API_BASE_URL=https://chaitanyasingh.org/apex-arena/api \
#   scripts/smoke-test-deployment.sh [room-slug]

set -uo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://chaitanyasingh.org/apex-arena}"
API_BASE_URL="${API_BASE_URL:-${PUBLIC_BASE_URL}/api}"
ROOM_SLUG="${1:-}"

PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
API_BASE_URL="${API_BASE_URL%/}"

PASS=0
FAIL=0
RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'

pass() { PASS=$((PASS + 1)); printf '  %sPASS%s %s\n' "$GREEN" "$RESET" "$1"; }
fail() {
  FAIL=$((FAIL + 1))
  printf '  %sFAIL%s %s\n' "$RED" "$RESET" "$1"
  [[ -n "${2:-}" ]] && printf '       %s%s%s\n' "$DIM" "$2" "$RESET"
}

section() { printf '\n%s\n' "$1"; }

# fetch <url> -> sets HTTP_STATUS, HTTP_BODY, HTTP_HEADERS
fetch() {
  local url="$1"; shift
  local headers_file body_file
  headers_file="$(mktemp)"; body_file="$(mktemp)"
  HTTP_STATUS="$(curl -sS -o "$body_file" -D "$headers_file" -w '%{http_code}' \
    --max-time 20 --location "$@" "$url" 2>/dev/null || echo "000")"
  HTTP_BODY="$(cat "$body_file")"
  HTTP_HEADERS="$(cat "$headers_file")"
  rm -f "$headers_file" "$body_file"
}

expect_status() {
  local label="$1" url="$2" expected="$3"
  fetch "$url"
  if [[ "$HTTP_STATUS" == "$expected" ]]; then
    pass "$label ($HTTP_STATUS)"
  else
    fail "$label" "expected HTTP $expected, got $HTTP_STATUS for $url"
  fi
}

printf 'Apex Arena deployment smoke test\n'
printf '  public : %s\n' "$PUBLIC_BASE_URL"
printf '  api    : %s\n' "$API_BASE_URL"

section 'Public pages'
expect_status 'landing page' "$PUBLIC_BASE_URL" 200
expect_status 'rooms index' "$PUBLIC_BASE_URL/rooms" 200
expect_status 'favicon' "$PUBLIC_BASE_URL/favicon.ico" 200

section 'Health endpoints'
expect_status 'liveness' "$API_BASE_URL/health/live" 200
fetch "$API_BASE_URL/health/ready"
if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "503" ]]; then
  pass "readiness reports a definite state ($HTTP_STATUS)"
else
  fail 'readiness' "expected 200 or 503, got $HTTP_STATUS"
fi
expect_status 'provider status' "$API_BASE_URL/health/provider" 200

section 'API contract'
fetch "$API_BASE_URL/rooms"
if [[ "$HTTP_STATUS" == "200" ]] && printf '%s' "$HTTP_BODY" | grep -q '"rooms"'; then
  pass 'rooms API returns JSON'
else
  fail 'rooms API' "status $HTTP_STATUS; body did not contain a rooms array"
fi

if [[ -n "$ROOM_SLUG" ]]; then
  expect_status "room deep link ($ROOM_SLUG)" "$PUBLIC_BASE_URL/rooms/$ROOM_SLUG" 200
  section 'SSE contract'
  SSE_HEADERS="$(curl -sS -D - -o /dev/null --max-time 8 \
    -H 'Accept: text/event-stream' \
    "$API_BASE_URL/rooms/$ROOM_SLUG/stream" 2>/dev/null || true)"
  if printf '%s' "$SSE_HEADERS" | grep -qi 'content-type: *text/event-stream'; then
    pass 'stream returns text/event-stream'
  else
    fail 'stream content type' 'expected Content-Type: text/event-stream'
  fi
  if printf '%s' "$SSE_HEADERS" | grep -qi 'cache-control:.*no-cache'; then
    pass 'stream is not cacheable'
  else
    fail 'stream cache-control' 'expected a no-cache Cache-Control header'
  fi
else
  printf '\n%sSkipping room deep link and SSE checks: pass a room slug as $1.%s\n' \
    "$DIM" "$RESET"
fi

section 'Origin and information leakage'
fetch "$PUBLIC_BASE_URL/rooms"
LEAKS=0
for pattern in 'railway.app' 'up.railway.app' 'vercel.app' 'neon.tech' 'upstash.io' 'workers.dev'; do
  if printf '%s' "$HTTP_BODY" | grep -qi "$pattern"; then
    fail "internal origin leaked: $pattern" 'infrastructure hostnames must not reach the browser'
    LEAKS=$((LEAKS + 1))
  fi
done
[[ "$LEAKS" -eq 0 ]] && pass 'no infrastructure hostname in the public HTML'

# The public URL must be served in place, never redirected to an origin host.
FINAL_URL="$(curl -sS -o /dev/null -w '%{url_effective}' --max-time 20 -L \
  "$PUBLIC_BASE_URL/rooms" 2>/dev/null || echo '')"
case "$FINAL_URL" in
  "$PUBLIC_BASE_URL"*) pass 'public URL is preserved (no redirect off-domain)' ;;
  '') fail 'redirect check' 'could not resolve the effective URL' ;;
  *) fail 'public URL was not preserved' "landed on $FINAL_URL" ;;
esac

section 'Production hardening'
expect_status 'debug config is disabled' "$API_BASE_URL/debug/config" 404

fetch "$API_BASE_URL/rooms"
if printf '%s' "$HTTP_BODY" | grep -qi 'day3-validation-room\|development_fixture'; then
  fail 'development fixtures are exposed' 'DEVELOPMENT_FIXTURE_ENABLED must be false'
else
  pass 'no development fixtures in public output'
fi

fetch "$PUBLIC_BASE_URL"
if printf '%s' "$HTTP_HEADERS" | grep -qi 'x-content-type-options: *nosniff'; then
  pass 'X-Content-Type-Options is set'
else
  fail 'X-Content-Type-Options' 'expected nosniff (configure at the Vercel edge)'
fi

printf '\n%s\n' '────────────────────────────────'
printf 'passed: %s   failed: %s\n' "$PASS" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf '%sSmoke test FAILED%s\n' "$RED" "$RESET"
  exit 1
fi
printf '%sSmoke test passed%s\n' "$GREEN" "$RESET"
