#!/bin/bash
# Pre-push validation hook — runs fast checks before pushing to main.
# Blocks the push if any check fails. ~30s total.
#
# Install: bash scripts/install-hooks.sh

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Only gate pushes to main
REMOTE="$1"
while read local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" != "refs/heads/main" ]]; then
        exit 0
    fi
done

PYTHON="venv/bin/python3"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

PASS="\033[32m✓\033[0m"
FAIL="\033[31m✗\033[0m"
BOLD="\033[1m"
RESET="\033[0m"

echo ""
echo "${BOLD}🔒 Pre-push validation (pushing to main)${RESET}"
echo "   This takes ~30s — you can keep working in another terminal."
echo ""

# ── 1. Syntax check changed Python files ─────────────────────────────────
echo -n "   Checking Python syntax... "
ERRORS=0
for f in $(git diff --name-only origin/main...HEAD -- '*.py' 2>/dev/null); do
    if [ -f "$f" ]; then
        if ! $PYTHON -c "import ast; ast.parse(open('$f').read())" 2>/dev/null; then
            echo ""
            echo -e "   ${FAIL} Syntax error in: $f"
            ERRORS=1
        fi
    fi
done
if [ $ERRORS -ne 0 ]; then
    echo -e "\n   ${FAIL} ${BOLD}Fix syntax errors before pushing.${RESET}"
    exit 1
fi
echo -e "${PASS}"

# ── 2. Import check — core modules load without error ────────────────────
echo -n "   Checking core imports... "
if $PYTHON -c "import service; import platforms; import api" 2>/dev/null; then
    echo -e "${PASS}"
else
    echo -e "${FAIL}"
    echo -e "   ${FAIL} ${BOLD}Core module import failed. Run: $PYTHON -c 'import service; import platforms; import api'${RESET}"
    exit 1
fi

# ── 3. Unit tests ────────────────────────────────────────────────────────
echo -n "   Running unit tests... "
if $PYTHON -m pytest test_webhook_events.py test_accounts.py -q --no-header 2>/dev/null | tail -1 | grep -q "passed"; then
    echo -e "${PASS}"
else
    echo -e "${FAIL}"
    echo -e "   ${FAIL} ${BOLD}Unit tests failed. Run: $PYTHON -m pytest test_webhook_events.py test_accounts.py -v${RESET}"
    exit 1
fi

# ── 4. API smoketest (only if server is running) ─────────────────────────
if curl -s --max-time 2 http://localhost:8100/api/v1/health > /dev/null 2>&1; then
    echo -n "   Checking CF Pages credentials... "
    if $PYTHON -c "
import asyncio
from helpers.api_smoketest import _check_cf_credentials
r = asyncio.run(_check_cf_credentials())
exit(0 if r.passed else 1)
" 2>/dev/null; then
        echo -e "${PASS}"
    else
        echo -e "${FAIL}"
        echo -e "   ${FAIL} ${BOLD}CF Pages credentials invalid or missing.${RESET}"
        exit 1
    fi

    echo -n "   Checking API health... "
    if curl -s --max-time 5 http://localhost:8100/api/v1/health | grep -q '"ok"'; then
        echo -e "${PASS}"
    else
        echo -e "${FAIL}"
        echo -e "   ${FAIL} ${BOLD}API health check failed.${RESET}"
        exit 1
    fi
else
    echo -e "   Skipping API checks (server not running)"
fi

echo ""
echo -e "   ${BOLD}${PASS} All checks passed — pushing to main${RESET}"
echo ""
