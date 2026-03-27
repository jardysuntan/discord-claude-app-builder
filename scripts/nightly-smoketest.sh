#!/bin/bash
# Nightly smoke test — runs full buildapp→demo cycle
# Results: posted to #smoke-tests + logged locally (gitignored)
cd /Users/jaredtanpersonal/bots/discord-claude-bridge
source .env
export SMOKETEST_CHANNEL_ID

LOGDIR="smoketest-results"
LOGFILE="$LOGDIR/$(date +%Y-%m-%d_%H%M).log"

/opt/homebrew/Cellar/python@3.12/3.12.12_2/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python -m commands.smoketest --scenario all --api 2>&1 | tee "$LOGFILE"

# Keep only last 30 days of logs
find "$LOGDIR" -name "*.log" -mtime +30 -delete
