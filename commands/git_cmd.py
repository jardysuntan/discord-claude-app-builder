"""
commands/git_cmd.py — Git and GitHub integration from Discord.

Commands:
  /status          → branch, changed files, ahead/behind
  /diff            → summary of changes since last commit
  /diff full       → full patch (truncated for Discord)
  /commit [msg]    → commit all changes (auto-generates msg if omitted)
  /undo            → revert last commit
  /log [n]         → recent commit history
  /branch [name]   → show or create+switch branch
  /stash           → stash current changes
  /stash pop       → restore stashed changes
  /pr [title]      → create GitHub PR from current branch
  /repo            → show remote info
  /repo create     → create GitHub repo and push
  /repo set <url>  → set remote origin
"""

import asyncio
import shlex
from typing import Optional, Callable, Awaitable

import config
from claude_runner import ClaudeRunner


async def _git(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    """Run a git command, return (exit_code, combined_output)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "Timed out."
    return proc.returncode, (out.decode(errors="replace") + err.decode(errors="replace")).strip()


async def _gh(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    """Run a GitHub CLI command."""
    proc = await asyncio.create_subprocess_exec(
        "gh", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "Timed out."
    return proc.returncode, (out.decode(errors="replace") + err.decode(errors="replace")).strip()


async def ensure_git_repo(ws_path: str) -> tuple[bool, str]:
    """Make sure the workspace is a git repo. Init if not."""
    rc, _ = await _git(["rev-parse", "--git-dir"], ws_path)
    if rc == 0:
        return True, "Already a git repo."
    rc, out = await _git(["init"], ws_path)
    if rc == 0:
        # Create .gitignore for KMP projects
        gitignore = (
            "# Build\nbuild/\n.gradle/\n*.iml\n.idea/\nlocal.properties\n"
            "# iOS\niosApp/Pods/\n*.xcworkspace/\n"
            "# OS\n.DS_Store\nThumbs.db\n"
        )
        import os
        gi_path = os.path.join(ws_path, ".gitignore")
        if not os.path.exists(gi_path):
            with open(gi_path, "w") as f:
                f.write(gitignore)
        await _git(["add", ".gitignore"], ws_path)
        await _git(["commit", "-m", "Initial commit with .gitignore"], ws_path)
        return True, "Initialized git repo."
    return False, f"Failed to init: {out}"


# ── Status ───────────────────────────────────────────────────────────────────

async def handle_status(ws_path: str, ws_key: str) -> str:
    rc, branch = await _git(["branch", "--show-current"], ws_path)
    if rc != 0:
        return "❌ Not a git repo. Changes will auto-init on first `/commit`."

    _, status = await _git(["status", "--short"], ws_path)
    _, ahead_behind = await _git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], ws_path)

    lines = [f"📊 **{ws_key}** — branch `{branch.strip()}`"]

    if ahead_behind and "\t" in ahead_behind:
        ahead, behind = ahead_behind.strip().split("\t")
        if int(ahead) > 0:
            lines.append(f"  ⬆️ {ahead} commit(s) ahead of remote")
        if int(behind) > 0:
            lines.append(f"  ⬇️ {behind} commit(s) behind remote")

    if status:
        changed = len(status.strip().splitlines())
        lines.append(f"  📝 {changed} file(s) changed")
        if changed <= 15:
            lines.append(f"```\n{status}\n```")
        else:
            short = "\n".join(status.splitlines()[:15])
            lines.append(f"```\n{short}\n…and {changed - 15} more\n```")
    else:
        lines.append("  ✨ Working tree clean")

    return "\n".join(lines)


# ── Diff ─────────────────────────────────────────────────────────────────────

async def handle_diff(ws_path: str, full: bool = False) -> str:
    if full:
        _, diff = await _git(["diff"], ws_path)
        if not diff:
            _, diff = await _git(["diff", "--cached"], ws_path)
        if not diff:
            return "No changes to show."
        if len(diff) > 1800:
            diff = diff[:1800] + "\n…(truncated)"
        return f"```diff\n{diff}\n```"
    else:
        _, stat = await _git(["diff", "--stat"], ws_path)
        if not stat:
            _, stat = await _git(["diff", "--cached", "--stat"], ws_path)
        return f"```\n{stat or 'No changes.'}\n```"


# ── Commit ───────────────────────────────────────────────────────────────────

async def handle_commit(
    ws_path: str,
    ws_key: str,
    message: Optional[str] = None,
    claude: Optional[ClaudeRunner] = None,
    auto_push: bool = False,
) -> str:
    # Ensure it's a repo
    ok, msg = await ensure_git_repo(ws_path)
    if not ok:
        return f"❌ {msg}"

    # Check if there are changes
    _, status = await _git(["status", "--porcelain"], ws_path)
    if not status.strip():
        return "✨ Nothing to commit — working tree clean."

    # Stage all changes
    await _git(["add", "-A"], ws_path)

    # Generate commit message if not provided
    if not message and claude:
        _, diff_stat = await _git(["diff", "--cached", "--stat"], ws_path)
        result = await claude.run(
            f"Generate a concise git commit message (one line, max 72 chars, "
            f"conventional commits style) for these changes:\n\n{diff_stat}",
            ws_key, ws_path,
        )
        message = result.stdout.strip().strip('"').strip("'").split("\n")[0][:72]

    if not message:
        message = "Update from discord-claude-bridge"

    rc, out = await _git(["commit", "-m", message], ws_path)
    if rc != 0:
        return f"❌ Commit failed:\n```\n{out}\n```"

    result = f"✅ Committed: `{message}`"

    # Auto-push if remote is set
    if auto_push:
        rc_push, push_out = await _git(["push"], ws_path, timeout=30)
        if rc_push == 0:
            result += "\n⬆️ Pushed to remote."
        else:
            result += f"\n⚠️ Push failed (no remote?): `{push_out[:200]}`"

    return result


# ── Auto-commit helper (called from buildapp/fix on success) ─────────────────

async def auto_commit_if_enabled(
    ws_path: str,
    ws_key: str,
    context: str,
    claude: Optional[ClaudeRunner] = None,
) -> Optional[str]:
    """
    Auto-commit after successful build. Returns commit message or None.
    Called internally by buildapp/fix flows.
    """
    _, status = await _git(["status", "--porcelain"], ws_path)
    if not status.strip():
        return None

    await _git(["add", "-A"], ws_path)

    # Generate message from context
    message = f"feat: {context}" if context else "auto: changes from discord-claude-bridge"
    if len(message) > 72:
        message = message[:69] + "..."

    rc, _ = await _git(["commit", "-m", message], ws_path)
    if rc == 0:
        # Try to push silently
        await _git(["push"], ws_path, timeout=15)
        return message
    return None


# ── Undo ─────────────────────────────────────────────────────────────────────

async def handle_undo(ws_path: str) -> str:
    # Show what will be reverted
    _, log = await _git(["log", "--oneline", "-1"], ws_path)
    if not log:
        return "❌ No commits to undo."

    rc, out = await _git(["revert", "HEAD", "--no-edit"], ws_path)
    if rc == 0:
        return f"↩️ Reverted: `{log.strip()}`"
    return f"❌ Revert failed:\n```\n{out[:500]}\n```"


# ── Log ──────────────────────────────────────────────────────────────────────

async def handle_log(ws_path: str, count: int = 10) -> str:
    count = min(count, 30)  # cap at 30
    _, log = await _git(
        ["log", f"--oneline", f"-{count}", "--decorate", "--no-color"],
        ws_path,
    )
    if not log:
        return "No commits yet."
    return f"```\n{log}\n```"


# ── Branch ───────────────────────────────────────────────────────────────────

async def handle_branch(ws_path: str, name: Optional[str] = None) -> str:
    if not name:
        # Show branches
        _, branches = await _git(["branch", "-a", "--no-color"], ws_path)
        return f"```\n{branches or 'No branches (not a git repo?)'}\n```"

    # Create and switch
    rc, out = await _git(["checkout", "-b", name], ws_path)
    if rc == 0:
        return f"🌿 Created and switched to branch `{name}`"

    # Maybe it already exists, just switch
    rc, out = await _git(["checkout", name], ws_path)
    if rc == 0:
        return f"🔀 Switched to branch `{name}`"
    return f"❌ Branch error:\n```\n{out}\n```"


# ── Stash ────────────────────────────────────────────────────────────────────

async def handle_stash(ws_path: str, pop: bool = False) -> str:
    if pop:
        rc, out = await _git(["stash", "pop"], ws_path)
        return f"📦 Stash popped." if rc == 0 else f"❌ {out}"
    else:
        rc, out = await _git(["stash", "push", "-m", "Stashed from Discord"], ws_path)
        return f"📦 Changes stashed." if rc == 0 else f"❌ {out}"


# ── PR ───────────────────────────────────────────────────────────────────────

async def handle_pr(
    ws_path: str,
    ws_key: str,
    title: Optional[str] = None,
    claude: Optional[ClaudeRunner] = None,
) -> str:
    # Check we're not on main
    _, branch = await _git(["branch", "--show-current"], ws_path)
    branch = branch.strip()
    if branch in ("main", "master"):
        return "⚠️ You're on `main`. Create a feature branch first with `/branch <name>`."

    # Push current branch
    rc, push_out = await _git(["push", "-u", "origin", branch], ws_path, timeout=30)
    if rc != 0:
        return f"❌ Push failed:\n```\n{push_out[:500]}\n```"

    # Generate title if not provided
    if not title:
        _, log = await _git(["log", "main..HEAD", "--oneline"], ws_path)
        if claude and log:
            result = await claude.run(
                f"Generate a concise PR title (max 60 chars) for these commits:\n{log}",
                ws_key, ws_path,
            )
            title = result.stdout.strip().strip('"').strip("'")[:60]
        else:
            title = f"Feature: {branch}"

    # Generate PR body from commit log
    _, log = await _git(["log", "main..HEAD", "--pretty=format:- %s"], ws_path)
    body = f"## Changes\n\n{log}\n\n---\n*Created from Discord via discord-claude-bridge*"

    # Create PR via gh CLI
    rc, out = await _gh(
        ["pr", "create", "--title", title, "--body", body],
        ws_path, timeout=30,
    )
    if rc == 0:
        # out usually contains the PR URL
        pr_url = out.strip().splitlines()[-1]
        return f"🔀 **PR created!**\n\n  `{title}`\n  👉 {pr_url}"
    return f"❌ PR creation failed:\n```\n{out[:500]}\n```"


# ── Repo ─────────────────────────────────────────────────────────────────────

async def handle_repo(ws_path: str, ws_key: str, sub: Optional[str] = None, arg: Optional[str] = None) -> str:
    if not sub:
        # Show remote
        _, remote = await _git(["remote", "-v"], ws_path)
        if remote:
            return f"```\n{remote}\n```"
        return "No remote set. Use `/repo create` or `/repo set <url>`."

    if sub == "create":
        # Create GitHub repo via gh CLI
        rc, out = await _gh(
            ["repo", "create", ws_key, "--private", "--source", ".", "--push"],
            ws_path, timeout=30,
        )
        if rc == 0:
            return f"✅ GitHub repo created and pushed!\n{out.strip()}"
        return f"❌ Failed:\n```\n{out[:500]}\n```"

    if sub == "set" and arg:
        rc, out = await _git(["remote", "add", "origin", arg], ws_path)
        if rc != 0:
            # Maybe origin exists, update it
            rc, out = await _git(["remote", "set-url", "origin", arg], ws_path)
        if rc == 0:
            return f"✅ Remote set to `{arg}`"
        return f"❌ {out}"

    return "`/repo` · `/repo create` · `/repo set <url>`"
