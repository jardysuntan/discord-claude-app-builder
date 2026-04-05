"""
helpers/autofix.py — Auto-fix PR on smoke test failure.

When a smoke test fails, this module:
1. Analyzes the failure (which stage broke, what error)
2. Creates a fix branch: autofix/smoke-YYYY-MM-DD
3. Runs Claude CLI with a targeted prompt based on the failure
4. If Claude makes changes, commits them and creates a PR via `gh pr create`
5. Returns the PR URL or None
"""

import logging
import subprocess
from datetime import date
from typing import Optional

import config
from helpers.smoketest_runner import SmokeTestResult

log = logging.getLogger(__name__)

BOT_REPO = "/Users/jaredtanpersonal/bots/discord-claude-bridge"
# Note: local directory is "discord-claude-bridge" but the GitHub repo is "discord-claude-app-builder".
GITHUB_REMOTE = "jardysuntan/discord-claude-app-builder"


def _first_failure(result: SmokeTestResult) -> tuple[str, str]:
    """Return (stage_name, detail) for the first failed stage."""
    for stage in result.stages:
        if not stage.passed:
            return stage.name, stage.detail
    return "unknown", ""


def _build_prompt(stage_name: str, detail: str) -> str:
    """Build a targeted Claude prompt based on the failure stage and error."""
    return (
        f"The nightly smoke test failed at stage: {stage_name}\n"
        f"Error details:\n{detail}\n\n"
        "The smoke test runs a full buildapp cycle: create KMP workspace, "
        "generate code with Claude, build Android, build Web, take screenshot.\n\n"
        "Investigate the bot's own codebase to find the root cause of this "
        "failure and fix it. The failure is in the bot infrastructure, not in "
        "generated app code. Look at helpers/smoketest_runner.py, agent_loop.py, "
        "platforms/, and related modules.\n\n"
        "Only change what is necessary to fix the issue. Do not refactor or "
        "add unrelated improvements."
    )


def attempt_autofix(result: SmokeTestResult) -> Optional[str]:
    """Analyze a smoke test failure and attempt an automated fix.

    Returns the PR URL if a fix was created, or None.
    """
    if result.success:
        return None

    stage_name, detail = _first_failure(result)
    if stage_name == "unknown":
        log.warning("autofix: no failed stage found in result")
        return None

    branch = f"autofix/smoke-{date.today().isoformat()}"
    log.info("autofix: failure at '%s', creating branch %s", stage_name, branch)

    try:
        # Ensure we're on a clean main branch before branching
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=BOT_REPO, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=BOT_REPO, check=True, capture_output=True, text=True,
        )

        # Delete local branch if it already exists (re-run on same day)
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=BOT_REPO, capture_output=True, text=True,
        )

        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=BOT_REPO, check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("autofix: git branch setup failed: %s", exc.stderr)
        return None

    # Run Claude CLI to attempt the fix
    prompt = _build_prompt(stage_name, detail)
    log.info("autofix: running Claude CLI on %s", BOT_REPO)
    try:
        subprocess.run(
            [
                config.CLAUDE_BIN,
                "--print",
                "--dangerously-skip-permissions",
                "-p", prompt,
            ],
            cwd=BOT_REPO,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        log.error("autofix: Claude CLI timed out")
        _cleanup_branch(branch)
        return None
    except subprocess.CalledProcessError as exc:
        log.error("autofix: Claude CLI failed: %s", exc.stderr[:500])
        _cleanup_branch(branch)
        return None

    # Check if Claude made any changes
    diff = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=BOT_REPO, capture_output=True, text=True,
    )
    if not diff.stdout.strip():
        log.info("autofix: Claude made no changes")
        _cleanup_branch(branch)
        return None

    # Commit and push
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=BOT_REPO, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m",
             f"autofix: smoke test failure at '{stage_name}'\n\n"
             f"Automated fix for nightly smoke test failure.\n"
             f"Stage: {stage_name}\n"
             f"Error: {detail[:200]}"],
            cwd=BOT_REPO, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=BOT_REPO, check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("autofix: commit/push failed: %s", exc.stderr)
        _cleanup_branch(branch)
        return None

    # Create PR via gh CLI
    try:
        pr_result = subprocess.run(
            [
                "gh", "pr", "create",
                "--repo", GITHUB_REMOTE,
                "--title", f"autofix: smoke test failure at '{stage_name}'",
                "--body",
                f"## Automated Fix\n\n"
                f"The nightly smoke test failed at **{stage_name}**.\n\n"
                f"```\n{detail[:500]}\n```\n\n"
                f"This PR was created automatically by the autofix system.\n\n"
                f"🤖 Generated by autofix",
                "--head", branch,
                "--base", "main",
            ],
            cwd=BOT_REPO,
            check=True, capture_output=True, text=True,
        )
        pr_url = pr_result.stdout.strip()
        log.info("autofix: PR created — %s", pr_url)
        subprocess.run(["git", "checkout", "main"], cwd=BOT_REPO, capture_output=True, text=True)
        return pr_url
    except subprocess.CalledProcessError as exc:
        log.error("autofix: gh pr create failed: %s", exc.stderr)
        return None


def _cleanup_branch(branch: str) -> None:
    """Switch back to main and delete the fix branch."""
    try:
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=BOT_REPO, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=BOT_REPO, capture_output=True, text=True,
        )
    except Exception:
        pass
