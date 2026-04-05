"""
helpers/error_reporter.py — Auto-file GitHub issues + async fix PRs.

When any unhandled error surfaces from the bot, this module:
1. Files a GitHub issue on the bot's own repo (with dedup)
2. Spawns a background Claude agent that investigates the codebase,
   creates a fix branch, and opens a PR linked to the issue.

The fix step reuses the same pattern as helpers/autofix.py but is
triggered by live errors rather than nightly smoke tests.
"""

import asyncio
import hashlib
import json
import logging
import subprocess
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

BOT_REPO = "/Users/jaredtanpersonal/bots/discord-claude-bridge"
GITHUB_REMOTE = "jardysuntan/discord-claude-app-builder"
DEDUP_STATE = Path(BOT_REPO) / "logs" / "auto_bug_dedup.json"
DEDUP_WINDOW_SECS = 6 * 60 * 60  # 6 hours


def _fingerprint(title: str, detail: str) -> str:
    """Stable hash of the error signature for dedup."""
    # Only hash the first 500 chars of detail — traceback tails (memory
    # addresses, timestamps) are noisy and break dedup.
    sig = f"{title}\n{detail[:500]}"
    return hashlib.md5(sig.encode("utf-8", errors="replace")).hexdigest()


def _load_dedup() -> dict:
    if not DEDUP_STATE.exists():
        return {}
    try:
        return json.loads(DEDUP_STATE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_dedup(state: dict) -> None:
    DEDUP_STATE.parent.mkdir(parents=True, exist_ok=True)
    DEDUP_STATE.write_text(json.dumps(state, indent=2))


def _recently_filed(fp: str) -> bool:
    state = _load_dedup()
    ts = state.get(fp)
    if ts is None:
        return False
    return (time.time() - ts) < DEDUP_WINDOW_SECS


def _record_filed(fp: str) -> None:
    state = _load_dedup()
    # Purge old entries so the file doesn't grow unbounded
    cutoff = time.time() - DEDUP_WINDOW_SECS
    state = {k: v for k, v in state.items() if v > cutoff}
    state[fp] = time.time()
    _save_dedup(state)


def _create_issue(title: str, detail: str, context: str) -> Optional[tuple[str, int]]:
    """Create a GitHub issue and return (url, number)."""
    body = (
        f"## Auto-filed bug\n\n"
        f"**Context:** {context}\n\n"
        f"### Error\n\n"
        f"```\n{detail[:3000]}\n```\n\n"
        f"---\n"
        f"_Filed automatically by the discord-claude-bridge error reporter. "
        f"A fix PR may be opened asynchronously._"
    )
    try:
        result = subprocess.run(
            [
                "gh", "issue", "create",
                "--repo", GITHUB_REMOTE,
                "--title", f"auto-bug: {title[:200]}",
                "--body", body,
                "--label", "auto-bug",
            ],
            cwd=BOT_REPO,
            check=True, capture_output=True, text=True,
            timeout=30,
        )
        url = result.stdout.strip()
        # gh returns e.g. https://github.com/owner/repo/issues/42
        number = int(url.rsplit("/", 1)[-1])
        return url, number
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "")[:500]
        # If the label doesn't exist, retry without it
        if "label" in stderr.lower() and "not found" in stderr.lower():
            try:
                result = subprocess.run(
                    [
                        "gh", "issue", "create",
                        "--repo", GITHUB_REMOTE,
                        "--title", f"auto-bug: {title[:200]}",
                        "--body", body,
                    ],
                    cwd=BOT_REPO,
                    check=True, capture_output=True, text=True,
                    timeout=30,
                )
                url = result.stdout.strip()
                number = int(url.rsplit("/", 1)[-1])
                return url, number
            except subprocess.CalledProcessError as exc2:
                log.error("error_reporter: gh issue create retry failed: %s",
                          (exc2.stderr or "")[:500])
                return None
        log.error("error_reporter: gh issue create failed: %s", stderr)
        return None
    except (subprocess.TimeoutExpired, ValueError) as exc:
        log.error("error_reporter: gh issue create error: %s", exc)
        return None


def _fix_prompt(title: str, detail: str, context: str, issue_num: int) -> str:
    return (
        f"A live error just occurred in the discord-claude-bridge bot.\n\n"
        f"Issue #{issue_num}: {title}\n"
        f"Context: {context}\n\n"
        f"Error detail:\n{detail[:2000]}\n\n"
        f"Investigate the bot's own codebase to find the root cause and fix it. "
        f"The failure is in the bot infrastructure itself. Look at the files in "
        f"commands/, handlers/, helpers/, platforms.py, service.py, bot.py, and "
        f"related modules.\n\n"
        f"Only change what is necessary to fix the issue. Do not refactor or "
        f"add unrelated improvements. Do not add comments unless the logic is "
        f"genuinely non-obvious."
    )


def _run_fix_pr(title: str, detail: str, context: str,
                issue_url: str, issue_num: int) -> Optional[str]:
    """Blocking: branch, run Claude, commit, push, open PR. Returns PR URL."""
    branch = f"auto-bug/{date.today().isoformat()}-{issue_num}"
    log.info("error_reporter: creating fix branch %s for issue #%d", branch, issue_num)

    try:
        subprocess.run(["git", "fetch", "origin", "main"],
                       cwd=BOT_REPO, check=True, capture_output=True, text=True, timeout=60)
        # Create branch from origin/main WITHOUT switching working tree away
        # from the user's current branch (avoids disturbing in-progress work).
        # We use a worktree instead.
    except subprocess.CalledProcessError as exc:
        log.error("error_reporter: git fetch failed: %s", (exc.stderr or "")[:300])
        return None

    # Use a worktree to isolate the fix attempt from the user's live checkout.
    worktree_dir = Path(BOT_REPO).parent / f".auto-bug-wt-{issue_num}"
    try:
        if worktree_dir.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree_dir)],
                           cwd=BOT_REPO, capture_output=True, text=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_dir), "origin/main"],
            cwd=BOT_REPO, check=True, capture_output=True, text=True, timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        log.error("error_reporter: worktree add failed: %s", (exc.stderr or "")[:300])
        return None

    try:
        prompt = _fix_prompt(title, detail, context, issue_num)
        try:
            subprocess.run(
                [
                    config.CLAUDE_BIN,
                    "--print",
                    "--dangerously-skip-permissions",
                    "-p", prompt,
                ],
                cwd=str(worktree_dir),
                check=True, capture_output=True, text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            log.error("error_reporter: Claude CLI timed out on issue #%d", issue_num)
            return None
        except subprocess.CalledProcessError as exc:
            log.error("error_reporter: Claude CLI failed: %s", (exc.stderr or "")[:500])
            return None

        diff = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(worktree_dir), capture_output=True, text=True,
        )
        if not diff.stdout.strip():
            log.info("error_reporter: Claude made no changes for issue #%d", issue_num)
            return None

        try:
            subprocess.run(["git", "add", "-A"],
                           cwd=str(worktree_dir), check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "commit", "-m",
                 f"auto-bug: fix for issue #{issue_num}\n\n"
                 f"Automated fix for: {title[:150]}\n\n"
                 f"Closes #{issue_num}"],
                cwd=str(worktree_dir), check=True, capture_output=True, text=True,
            )
            # Push using gh-issued token (remote may be HTTPS while gh is
            # configured for SSH, which leaves git without HTTPS creds).
            try:
                token = subprocess.run(
                    ["gh", "auth", "token"],
                    cwd=BOT_REPO, check=True, capture_output=True, text=True, timeout=10,
                ).stdout.strip()
            except subprocess.CalledProcessError:
                token = ""
            if token:
                push_url = f"https://x-access-token:{token}@github.com/{GITHUB_REMOTE}.git"
                subprocess.run(
                    ["git", "push", push_url, f"HEAD:refs/heads/{branch}"],
                    cwd=str(worktree_dir), check=True, capture_output=True, text=True, timeout=120,
                )
            else:
                subprocess.run(
                    ["git", "push", "-u", "origin", branch],
                    cwd=str(worktree_dir), check=True, capture_output=True, text=True, timeout=120,
                )
        except subprocess.CalledProcessError as exc:
            log.error("error_reporter: commit/push failed: %s", (exc.stderr or "")[:300])
            return None

        try:
            pr_result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--repo", GITHUB_REMOTE,
                    "--title", f"auto-bug fix: {title[:150]}",
                    "--body",
                    f"## Automated Fix for #{issue_num}\n\n"
                    f"{issue_url}\n\n"
                    f"Closes #{issue_num}\n\n"
                    f"This PR was created automatically in response to a live error. "
                    f"Review carefully before merging.\n\n"
                    f"🤖 Generated by auto-bug reporter",
                    "--head", branch,
                    "--base", "main",
                ],
                cwd=str(worktree_dir),
                check=True, capture_output=True, text=True, timeout=30,
            )
            pr_url = pr_result.stdout.strip()
            log.info("error_reporter: PR created — %s", pr_url)
            return pr_url
        except subprocess.CalledProcessError as exc:
            log.error("error_reporter: gh pr create failed: %s", (exc.stderr or "")[:300])
            return None
    finally:
        # Clean up the worktree and local branch
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree_dir)],
                           cwd=BOT_REPO, capture_output=True, text=True)
            subprocess.run(["git", "branch", "-D", branch],
                           cwd=BOT_REPO, capture_output=True, text=True)
        except Exception:
            pass


async def report_error_and_fix(title: str, detail: str, context: str = "") -> None:
    """Primary entry point. Files an issue and spawns a background fix attempt.

    Safe to call from any error handler; swallows all exceptions internally.

    Args:
        title: short human-readable error label (e.g. "export IPA timed out")
        detail: full error detail / traceback
        context: optional context string (command name, user, workspace)
    """
    try:
        fp = _fingerprint(title, detail)
        if _recently_filed(fp):
            log.info("error_reporter: skipping duplicate within dedup window (fp=%s)", fp[:8])
            return
        _record_filed(fp)

        # File the issue synchronously (fast — single gh call)
        issue = await asyncio.to_thread(_create_issue, title, detail, context)
        if issue is None:
            log.warning("error_reporter: issue creation failed, skipping fix")
            return
        issue_url, issue_num = issue
        log.info("error_reporter: filed issue #%d — %s", issue_num, issue_url)

        # Fire-and-forget the fix attempt (runs Claude CLI, can take minutes)
        async def _bg():
            try:
                pr_url = await asyncio.to_thread(
                    _run_fix_pr, title, detail, context, issue_url, issue_num
                )
                if pr_url:
                    log.info("error_reporter: auto-fix PR ready — %s", pr_url)
            except Exception as exc:  # noqa: BLE001
                log.exception("error_reporter: background fix crashed: %s", exc)

        asyncio.create_task(_bg())
    except Exception as exc:  # noqa: BLE001 — never let the reporter crash the caller
        log.exception("error_reporter: unexpected failure: %s", exc)


def format_exception(exc: BaseException) -> tuple[str, str]:
    """Build (title, detail) from an exception for report_error_and_fix."""
    title = f"{type(exc).__name__}: {str(exc)[:120]}"
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return title, detail
