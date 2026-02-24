"""
commands/queue.py — Queue multiple Claude tasks for sequential execution.

/queue
add dark mode support
---
improve error messages when build fails
---
add a /logs command that shows recent pm2 logs
"""

import time
from typing import Callable, Awaitable, Optional

import config
from claude_runner import ClaudeRunner
from cost_tracker import CostTracker


def parse_queue_tasks(raw: str) -> list[str]:
    """Split raw input on --- separators, stripping blanks."""
    parts = raw.split("---")
    return [t.strip() for t in parts if t.strip()]


async def handle_queue(
    raw: str,
    workspace_key: str,
    workspace_path: str,
    claude: ClaudeRunner,
    cost_tracker: CostTracker,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
) -> None:
    tasks = parse_queue_tasks(raw)
    if not tasks:
        await on_status("Usage: `/queue task1 --- task2 --- task3`", None)
        return

    cap = config.DAILY_TOKEN_CAP_USD
    pct = config.QUEUE_STOP_PCT / 100.0

    await on_status(
        f"📋 **Queue started** — {len(tasks)} task(s) in **{workspace_key}**\n"
        f"  Budget: ${cap:.2f}/day ({config.QUEUE_STOP_PCT}% cap)\n"
        f"  Already spent today: ${cost_tracker.today_spent():.2f}",
        None,
    )

    completed = 0
    failed = 0
    skipped = 0
    total_cost = 0.0
    start_time = time.time()

    for i, task in enumerate(tasks, 1):
        # Budget check before each task
        if not cost_tracker.can_afford(cap, pct):
            skipped = len(tasks) - i + 1
            await on_status(
                f"⛔ **Budget limit reached** — ${cost_tracker.today_spent():.2f} "
                f"spent (cap: ${cap:.2f} × {config.QUEUE_STOP_PCT}%)\n"
                f"Skipping {skipped} remaining task(s).",
                None,
            )
            break

        # Fresh session per task to avoid context compaction crashes
        claude.clear_session(workspace_key)

        preview = task[:100] + "…" if len(task) > 100 else task
        await on_status(f"▶️ **Task {i}/{len(tasks)}:** {preview}", None)

        async def progress(msg):
            await on_status(msg, None)

        result = await claude.run(
            task, workspace_key, workspace_path, on_progress=progress,
        )

        cost_tracker.add(result.total_cost_usd)
        total_cost += result.total_cost_usd

        if result.exit_code != 0:
            failed += 1
            error_preview = result.stderr[:500] if result.stderr else "(no stderr)"
            await on_status(
                f"⚠️ Task {i} failed (exit {result.exit_code}):\n```\n{error_preview}\n```",
                None,
            )
        else:
            completed += 1
            output_preview = result.stdout[:500] if result.stdout else "(no output)"
            await on_status(f"✅ Task {i} done (${result.total_cost_usd:.4f})", None)

    # Final summary
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    summary_parts = [
        f"📊 **Queue complete** — {mins}m {secs}s",
        f"  ✅ Completed: {completed}",
    ]
    if failed:
        summary_parts.append(f"  ⚠️ Failed: {failed}")
    if skipped:
        summary_parts.append(f"  ⏭️ Skipped: {skipped}")
    summary_parts.append(f"  💰 Queue cost: ${total_cost:.4f}")
    summary_parts.append(f"  📈 Today total: ${cost_tracker.today_spent():.2f} / ${cap:.2f}")

    await on_status("\n".join(summary_parts), None)
