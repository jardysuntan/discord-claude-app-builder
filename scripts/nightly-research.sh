#!/usr/bin/env bash
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT_DIR="/Users/jaredtanpersonal/bots/discord-claude-bridge"
LOCKFILE="/tmp/nightly-research.lock"
DATE=$(date +%Y-%m-%d)
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/nightly-research-${DATE}.log"
STAGE_TIMEOUT=1800  # 30 minutes per stage
MAX_TURNS=30
RESEARCH_OUTPUT=$(mktemp /tmp/nightly-research-output.XXXXXX.json)

mkdir -p "$LOG_DIR"

# ── Logging ─────────────────────────────────────────────────────────────────
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ── Timeout (macOS doesn't have GNU timeout) ────────────────────────────────
run_with_timeout() {
    local secs=$1; shift
    "$@" &
    local pid=$!
    ( sleep "$secs" && kill "$pid" 2>/dev/null ) &
    local watchdog=$!
    wait "$pid" 2>/dev/null
    local rc=$?
    kill "$watchdog" 2>/dev/null
    wait "$watchdog" 2>/dev/null
    return $rc
}

# ── Cleanup ─────────────────────────────────────────────────────────────────
cleanup() {
    rm -f "$LOCKFILE" "$RESEARCH_OUTPUT"
    log "Cleanup done."
}
trap cleanup EXIT

# ── Lockfile guard ──────────────────────────────────────────────────────────
if [ -f "$LOCKFILE" ]; then
    log "ERROR: Lockfile exists ($LOCKFILE). Another run may be in progress. Exiting."
    exit 1
fi
echo $$ > "$LOCKFILE"

# ── Purge logs older than 30 days ───────────────────────────────────────────
find "$LOG_DIR" -name "nightly-research-*.log" -mtime +30 -delete 2>/dev/null || true

log "=== Nightly Research Started ==="

# ── Stage 1: Research ───────────────────────────────────────────────────────
log "Stage 1: Running competitive research..."

if ! run_with_timeout "$STAGE_TIMEOUT" bash -c \
    'cat "$1" | claude -p --max-turns "$2" --allowedTools "WebSearch" "WebFetch"' \
    _ "$PROJECT_DIR/scripts/research-prompt.md" "$MAX_TURNS" \
    > "$RESEARCH_OUTPUT" 2>> "$LOG_FILE"; then
    log "ERROR: Stage 1 failed or timed out."
    exit 1
fi

log "Stage 1 complete. Output saved to $RESEARCH_OUTPUT"

# ── Parse research output ──────────────────────────────────────────────────
# Extract the JSON block from Claude's response
RESEARCH_JSON=$(python3 -c "
import json, sys, re

text = open('$RESEARCH_OUTPUT').read()

# Find JSON block (may be in a code fence or bare)
match = re.search(r'\{[^{}]*\"actionable\"[^{}]*\}', text, re.DOTALL)
if not match:
    # Try to find a larger JSON block
    match = re.search(r'^\{.*\}$', text, re.DOTALL | re.MULTILINE)
if not match:
    print(json.dumps({'actionable': False, 'summary': 'Could not parse research output.'}))
    sys.exit(0)

try:
    data = json.loads(match.group())
    print(json.dumps(data))
except json.JSONDecodeError:
    print(json.dumps({'actionable': False, 'summary': 'JSON parse error.'}))
" 2>> "$LOG_FILE")

ACTIONABLE=$(echo "$RESEARCH_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('actionable', False))")

log "Research actionable: $ACTIONABLE"
log "Summary: $(echo "$RESEARCH_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('summary', 'N/A')[:200])")"

if [ "$ACTIONABLE" != "True" ] && [ "$ACTIONABLE" != "true" ]; then
    log "Nothing actionable found. Skipping Stage 2."
    log "=== Nightly Research Complete (no action) ==="
    exit 0
fi

# ── Stage 2: Implement ─────────────────────────────────────────────────────
log "Stage 2: Implementing feature..."

FEATURE_NAME=$(echo "$RESEARCH_JSON" | python3 -c "
import json, sys, re
data = json.load(sys.stdin)
name = data.get('feature', {}).get('name', 'update') if isinstance(data.get('feature'), dict) else 'update'
# Slugify
slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
print(slug[:40])
")
BRANCH_NAME="research/${DATE}-${FEATURE_NAME}"

cd "$PROJECT_DIR"

# Build the prompt by substituting placeholders
IMPLEMENT_PROMPT=$(python3 -c "
import sys
template = open('$PROJECT_DIR/scripts/implement-prompt.md').read()
template = template.replace('{{RESEARCH_SUMMARY}}', sys.argv[1])
template = template.replace('{{BRANCH_NAME}}', sys.argv[2])
print(template)
" "$RESEARCH_JSON" "$BRANCH_NAME")

if ! run_with_timeout "$STAGE_TIMEOUT" bash -c \
    'echo "$1" | claude -p --max-turns "$2" --allowedTools "Bash" "Read" "Write" "Edit" "Glob" "Grep"' \
    _ "$IMPLEMENT_PROMPT" "$MAX_TURNS" \
    >> "$LOG_FILE" 2>&1; then
    log "ERROR: Stage 2 failed or timed out."
    exit 1
fi

log "Stage 2 complete."
log "=== Nightly Research Complete ==="
