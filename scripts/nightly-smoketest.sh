#!/bin/bash
# Nightly smoke test — runs full buildapp→demo cycle
# Results: posted to #smoke-tests + logged locally (gitignored)
cd /Users/jaredtanpersonal/bots/discord-claude-bridge

# Ensure claude CLI and homebrew tools are on PATH (LaunchAgents get minimal PATH)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

source .env
export SMOKETEST_CHANNEL_ID

LOGDIR="smoketest-results"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/$(date +%Y-%m-%d_%H%M).log"

./venv/bin/python -m commands.smoketest --scenario all --api 2>&1 | tee "$LOGFILE"

# Keep only last 30 days of logs
find "$LOGDIR" -name "*.log" -mtime +30 -delete
