#!/usr/bin/env bash
# Smoke test for POST /api/v1/extract
#
# Usage:
#   export BRIDGE_TOKEN="sk_live_..."              # your bridge API key
#   export LLM_API_KEY="sk-ant-... | gsk_... | AIza..."   # any supported provider key
#   export BRIDGE_URL="http://localhost:8100"      # optional; defaults to localhost:8100
#   ./scripts/smoketest_extract.sh
#
# What it does:
#   1. Ensures the bridge account has an LLM credential set (uploads $LLM_API_KEY)
#   2. Calls /api/v1/extract with a tiny document and schema
#   3. Prints the structured JSON response
set -euo pipefail

: "${BRIDGE_TOKEN:?Set BRIDGE_TOKEN to your bridge API key (sk_live_...)}"
: "${LLM_API_KEY:?Set LLM_API_KEY to an Anthropic/OpenAI/Google/Groq/etc. key}"
BRIDGE_URL="${BRIDGE_URL:-http://localhost:8100}"

echo "==> Setting LLM credential on bridge account"
curl -sS -X POST "$BRIDGE_URL/api/v1/account/credentials/llm" \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"data\": {\"api_key\": \"$LLM_API_KEY\"}}" \
  | python3 -m json.tool

echo
echo "==> Calling /api/v1/extract"
curl -sS -X POST "$BRIDGE_URL/api/v1/extract" \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Our Paris trip runs June 10-15, 2026. We are visiting the Louvre on day 1, Eiffel Tower on day 2, and Musee d Orsay on day 3. Members: Alice, Bob, Carol.",
    "json_schema": {
      "type": "object",
      "properties": {
        "trip": {
          "type": "object",
          "properties": {
            "destination": {"type": "string"},
            "start_date": {"type": "string", "description": "ISO 8601 date"},
            "end_date": {"type": "string", "description": "ISO 8601 date"}
          }
        },
        "venues": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "day": {"type": "integer"}}
          }
        },
        "members": {"type": "array", "items": {"type": "string"}}
      }
    }
  }' \
  | python3 -m json.tool
