"""
scripts/verify_error_reporter.py — smoke-test the auto-bug reporter.

Calls report_error_and_fix() with a synthetic error and waits for the
background fix attempt to complete so we can observe issue + PR creation.

Usage:
    python3 scripts/verify_error_reporter.py

Cleanup afterward:
    gh issue list --repo jardysuntan/discord-claude-app-builder --label auto-bug
    gh issue close <N> --repo ...
    gh pr close <N> --repo ... --delete-branch
"""

import asyncio
import logging
import sys
from pathlib import Path

# Allow running from repo root: python3 scripts/verify_error_reporter.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.error_reporter import report_error_and_fix, _load_dedup, _save_dedup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

SYNTHETIC_TITLE = "VERIFY: auto-bug reporter smoke test"
SYNTHETIC_DETAIL = """This is a synthetic error used to verify the auto-bug reporter plumbing.

The comment block at the top of helpers/error_reporter.py mentions
"discord-claude-bridge" but the repo on GitHub is actually named
"discord-claude-app-builder". Consider adding a one-line clarifying
comment near the GITHUB_REMOTE constant noting that the local directory
name differs from the GitHub repo name, to help future readers who grep
for the repo name and are confused.

This is a documentation-only change. Do not modify any logic. If you
determine no change is warranted, make no changes and exit.
"""
SYNTHETIC_CONTEXT = "verification script — not a real error"


async def main() -> int:
    # Clear any prior dedup entry for this title so reruns work
    state = _load_dedup()
    from helpers.error_reporter import _fingerprint
    fp = _fingerprint(SYNTHETIC_TITLE, SYNTHETIC_DETAIL)
    if fp in state:
        print(f"→ clearing stale dedup entry for fingerprint {fp[:8]}")
        del state[fp]
        _save_dedup(state)

    print("→ calling report_error_and_fix() ...")
    await report_error_and_fix(
        title=SYNTHETIC_TITLE,
        detail=SYNTHETIC_DETAIL,
        context=SYNTHETIC_CONTEXT,
    )
    print("→ report_error_and_fix() returned; background task spawned")
    print("→ waiting up to 12 minutes for background fix task to finish...")
    print("  (watch logs for 'auto-fix PR ready' or 'made no changes')")

    # Wait for any pending tasks (the background _bg task) to finish
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=720)
        except asyncio.TimeoutError:
            print("⚠️  background task did not finish in 12m — aborting")
            return 1

    print("✅ verification complete — check GitHub for the new issue (and PR, if any)")
    print("    gh issue list --repo jardysuntan/discord-claude-app-builder --label auto-bug")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
