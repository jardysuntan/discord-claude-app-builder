"""
commands/try_variants.py — Parallel agent builds with branch isolation.

Spawns multiple Claude Code sessions, each on its own git branch with a
different approach prompt.  After all variants finish, screenshots are
captured and posted as a Discord embed grid.  The user picks a winner via
emoji reactions; the winning branch is merged back and losing branches are
archived.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

import discord

import config
from agent_loop import run_agent_loop, format_loop_summary
from agent_protocol import AgentRunner
from commands.git_cmd import _git, ensure_git_repo
from helpers.budget import BudgetTracker
from helpers.web_screenshot import take_web_screenshot
from platforms import WebPlatform

# Reaction emoji for variant selection
VARIANT_EMOJIS = ["1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3"]

# Default approach labels when the user doesn't supply explicit variants
DEFAULT_APPROACHES = [
    "Use Material3 design with default theming",
    "Use a custom Compose theme with branded colours and typography",
    "Use platform-native styling for each target",
]


@dataclass
class VariantResult:
    index: int
    branch: str
    label: str
    success: bool
    screenshot_path: Optional[str] = None
    error_message: str = ""
    worktree_path: str = ""


@dataclass
class TryVariantsResult:
    variants: list[VariantResult] = field(default_factory=list)
    original_branch: str = "main"


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------

async def _current_branch(ws_path: str) -> str:
    rc, branch = await _git(["branch", "--show-current"], ws_path)
    return branch.strip() if rc == 0 else "main"


async def _create_variant_worktree(
    ws_path: str, variant_index: int, base_branch: str,
) -> tuple[str, str]:
    """Create a git worktree for variant *variant_index*.

    Returns (worktree_path, branch_name).
    """
    branch = f"variant-{variant_index}"
    worktree_dir = os.path.join(
        tempfile.gettempdir(), f"variant-{variant_index}-{os.path.basename(ws_path)}"
    )
    # Clean up any stale worktree at this path
    if os.path.exists(worktree_dir):
        await _git(["worktree", "remove", "--force", worktree_dir], ws_path)
        if os.path.exists(worktree_dir):
            shutil.rmtree(worktree_dir, ignore_errors=True)

    # Delete branch if it already exists (leftover from previous run)
    await _git(["branch", "-D", branch], ws_path)

    rc, out = await _git(
        ["worktree", "add", "-b", branch, worktree_dir, base_branch], ws_path
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create worktree for {branch}: {out}")
    return worktree_dir, branch


async def _remove_variant_worktree(ws_path: str, worktree_dir: str) -> None:
    await _git(["worktree", "remove", "--force", worktree_dir], ws_path)
    if os.path.exists(worktree_dir):
        shutil.rmtree(worktree_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Single variant runner
# ---------------------------------------------------------------------------

async def _run_variant(
    variant_index: int,
    label: str,
    prompt: str,
    ws_key: str,
    ws_path: str,
    base_branch: str,
    claude: AgentRunner,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> VariantResult:
    """Run a single variant in its own worktree + branch."""
    result = VariantResult(index=variant_index, branch="", label=label, success=False)

    try:
        worktree_dir, branch = await _create_variant_worktree(
            ws_path, variant_index, base_branch,
        )
        result.branch = branch
        result.worktree_path = worktree_dir

        variant_ws_key = f"{ws_key}__variant{variant_index}"

        budget = BudgetTracker(
            max_cost_usd=config.MAX_FIX_BUDGET_USD,
            max_invocations=config.MAX_TOTAL_INVOCATIONS,
        )

        full_prompt = (
            f"APPROACH: {label}\n\n{prompt}\n\n"
            f"Follow the approach described above."
        )

        if on_status:
            await on_status(f"🔀 Variant {variant_index + 1} ({label}) — starting Claude session...")

        loop_result = await run_agent_loop(
            initial_prompt=full_prompt,
            workspace_key=variant_ws_key,
            workspace_path=worktree_dir,
            claude=claude,
            platform="web",
            on_status=on_status,
            budget=budget,
        )

        if not loop_result.success:
            result.error_message = loop_result.final_message[:500]
            return result

        # Commit changes in the worktree
        await _git(["add", "-A"], worktree_dir)
        await _git(
            ["commit", "-m", f"variant {variant_index + 1}: {label[:50]}"],
            worktree_dir,
        )

        # Try to build web and take screenshot
        build = await WebPlatform.build(worktree_dir)
        if build.success:
            url = await WebPlatform.serve(worktree_dir, variant_ws_key)
            if url:
                await asyncio.sleep(2)
                local_url = f"http://localhost:{config.WEB_SERVE_PORT}"
                shot = await take_web_screenshot(local_url)
                if shot:
                    # Copy to unique path so multiple variants don't clobber each other
                    unique = os.path.join(
                        tempfile.gettempdir(),
                        f"variant_{variant_index}_preview.png",
                    )
                    shutil.copy2(shot, unique)
                    result.screenshot_path = unique

        result.success = True

    except Exception as exc:
        result.error_message = str(exc)[:500]

    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_try_variants(
    prompt: str,
    ws_key: str,
    ws_path: str,
    claude: AgentRunner,
    variant_count: int = 2,
    approach_labels: Optional[list[str]] = None,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> TryVariantsResult:
    """Run *variant_count* Claude sessions in parallel, each on its own branch."""
    ok, msg = await ensure_git_repo(ws_path)
    if not ok:
        raise RuntimeError(f"Git init failed: {msg}")

    # Auto-commit any dirty state so worktrees start clean
    _, status = await _git(["status", "--porcelain"], ws_path)
    if status.strip():
        await _git(["add", "-A"], ws_path)
        await _git(["commit", "-m", "auto: snapshot before try-variants"], ws_path)

    base_branch = await _current_branch(ws_path)

    labels = (approach_labels or DEFAULT_APPROACHES)[:variant_count]
    while len(labels) < variant_count:
        labels.append(f"Approach {len(labels) + 1}")

    output = TryVariantsResult(original_branch=base_branch)

    # Create per-variant status callbacks that tag messages with the variant number
    def _make_status_cb(idx: int):
        async def _cb(text: str):
            if on_status:
                await on_status(f"[Variant {idx + 1}] {text}")
        return _cb

    tasks = [
        _run_variant(
            variant_index=i,
            label=labels[i],
            prompt=prompt,
            ws_key=ws_key,
            ws_path=ws_path,
            base_branch=base_branch,
            claude=claude,
            on_status=_make_status_cb(i),
        )
        for i in range(variant_count)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            output.variants.append(VariantResult(
                index=len(output.variants),
                branch="",
                label="(error)",
                success=False,
                error_message=str(r)[:500],
            ))
        else:
            output.variants.append(r)

    return output


# ---------------------------------------------------------------------------
# Merge winner + cleanup
# ---------------------------------------------------------------------------

async def merge_winner(
    ws_path: str,
    winner: VariantResult,
    all_variants: list[VariantResult],
) -> str:
    """Merge the winning variant branch into the base branch and clean up."""
    # Switch back to the original branch
    base_branch = (await _current_branch(ws_path)) or "main"
    rc, out = await _git(["checkout", base_branch], ws_path)
    if rc != 0:
        # Might already be on it
        pass

    # Merge the winner
    rc, out = await _git(["merge", winner.branch, "--no-ff", "-m",
                          f"Merge variant {winner.index + 1}: {winner.label[:50]}"],
                         ws_path, timeout=30)
    if rc != 0:
        return f"❌ Merge failed:\n```\n{out[:500]}\n```"

    # Cleanup: remove worktrees and archive losing branches
    for v in all_variants:
        if v.worktree_path:
            await _remove_variant_worktree(ws_path, v.worktree_path)
        if v.branch and v.branch != winner.branch:
            # Archive by renaming, then delete
            await _git(["branch", "-m", v.branch, f"archive/{v.branch}"], ws_path)

    return (
        f"✅ **Variant {winner.index + 1}** ({winner.label}) merged!\n"
        f"Losing branches archived as `archive/variant-*`."
    )


# ---------------------------------------------------------------------------
# Discord embed builder
# ---------------------------------------------------------------------------

def build_variants_embed(
    ws_key: str,
    variants: list[VariantResult],
) -> discord.Embed:
    """Build a Discord embed summarising the variant results."""
    embed = discord.Embed(
        title=f"🏁 Variant Results — {ws_key}",
        description="React to pick the winner!",
        color=0x5865F2,
    )
    for v in variants:
        emoji = VARIANT_EMOJIS[v.index] if v.index < len(VARIANT_EMOJIS) else f"{v.index + 1}."
        status = "✅ Built" if v.success else f"❌ Failed"
        embed.add_field(
            name=f"{emoji} {v.label}",
            value=f"{status} — branch `{v.branch}`",
            inline=False,
        )
    embed.set_footer(text="React with 1️⃣ 2️⃣ 3️⃣ to select the winning variant.")
    return embed
