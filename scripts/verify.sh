#!/usr/bin/env bash
# End-to-end smoke test: replays the full OAuth flow a real client would go
# through (dynamic client registration -> login -> authorize -> token
# exchange -> call /mcp) and asserts the mysql_query tool comes back
# read-only. Run this after `docker compose up -d` to confirm the whole
# chain (edge -> authproxy -> mcp -> MySQL) actually works before wiring up
# a real client.
#
# Usage: scripts/verify.sh <ip-or-domain> [password]
# If password is omitted, reads AUTH_PASSWORD from .env in the repo root.

set -euo pipefail

ADDRESS="${1:?Usage: scripts/verify.sh <ip-or-domain> [password]}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -n "${2:-}" ]; then
  PASSWORD="$2"
else
  if [ ! -f "$ROOT_DIR/.env" ]; then
    echo "No .env found and no password given. Run generate-env.py first, or pass a password as the 2nd argument." >&2
    exit 1
  fi
  PASSWORD="$(grep '^AUTH_PASSWORD=' "$ROOT_DIR/.env" | cut -d= -f2-)"
fi

BASE_URL="https://${ADDRESS}"
COOKIES="$(mktemp)"
trap 'rm -f "$COOKIES"' EXIT

echo "--- register a dynamic client ---"
REG=$(curl -sk -X POST "${BASE_URL}/.idp/register" \
  -H "Content-Type: application/json" \
  -d '{"redirect_uris":["http://localhost/cb"],"client_name":"verify-script","token_endpoint_auth_method":"none"}')
CLIENT_ID=$(echo "$REG" | grep -o '"client_id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$CLIENT_ID" ]; then
  echo "Client registration failed. Response: $REG" >&2
  exit 1
fi
echo "client_id=$CLIENT_ID"

VERIFIER=$(openssl rand -base64 48 | tr -d '=+/' | tr -d '\n')
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -sha256 -binary | openssl base64 | tr '+/' '-_' | tr -d '=' | tr -d '\n')
STATE="verifyscriptstate$(date +%s)"

echo "--- kickoff auth (expect redirect to login) ---"
curl -sk -c "$COOKIES" -o /dev/null \
  "${BASE_URL}/.idp/auth?response_type=code&client_id=${CLIENT_ID}&redirect_uri=http%3A%2F%2Flocalhost%2Fcb&code_challenge=${CHALLENGE}&code_challenge_method=S256&state=${STATE}"

echo "--- login ---"
curl -sk -b "$COOKIES" -c "$COOKIES" -o /dev/null \
  -X POST "${BASE_URL}/.auth/login" --data-urlencode "password=${PASSWORD}"

echo "--- re-hit auth (now authenticated, get session id) ---"
SESSION_URL=$(curl -sk -b "$COOKIES" -c "$COOKIES" -o /dev/null -w '%{redirect_url}' \
  "${BASE_URL}/.idp/auth?response_type=code&client_id=${CLIENT_ID}&redirect_uri=http%3A%2F%2Flocalhost%2Fcb&code_challenge=${CHALLENGE}&code_challenge_method=S256&state=${STATE}")
SESSION_PATH=$(echo "$SESSION_URL" | sed -E 's#^https?://[^/]+##')
if [ -z "$SESSION_PATH" ]; then
  echo "Login didn't produce an authorize session. Check the password." >&2
  exit 1
fi

echo "--- submit authorize ---"
CODE_URL=$(curl -sk -b "$COOKIES" -c "$COOKIES" -o /dev/null -w '%{redirect_url}' -X POST "${BASE_URL}${SESSION_PATH}")
CODE=$(echo "$CODE_URL" | grep -o 'code=[^&]*' | cut -d= -f2)
if [ -z "$CODE" ]; then
  echo "Authorize step didn't return a code. Redirect was: $CODE_URL" >&2
  exit 1
fi

echo "--- token exchange ---"
TOKRESP=$(curl -sk -X POST "${BASE_URL}/.idp/token" \
  -d "grant_type=authorization_code" \
  -d "code=${CODE}" \
  -d "redirect_uri=http://localhost/cb" \
  -d "client_id=${CLIENT_ID}" \
  -d "code_verifier=${VERIFIER}")
ACCESS_TOKEN=$(echo "$TOKRESP" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
if [ -z "$ACCESS_TOKEN" ]; then
  echo "Token exchange failed. Response: $TOKRESP" >&2
  exit 1
fi

echo "--- call /mcp ---"
MCP_RESPONSE=$(curl -sk -X POST "${BASE_URL}/mcp" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verify-script","version":"1.0"}}}')

if echo "$MCP_RESPONSE" | grep -q 'mysql_query' && echo "$MCP_RESPONSE" | grep -q '"readOnlyHint":true'; then
  echo ""
  echo "PASS: full OAuth flow works end-to-end, mysql_query tool is present and read-only."
  exit 0
else
  echo ""
  echo "FAIL: /mcp did not return the expected tool. Response:" >&2
  echo "$MCP_RESPONSE" >&2
  exit 1
fi
