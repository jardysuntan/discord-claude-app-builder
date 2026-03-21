"""commands/status_cmd.py — /dashboard command: build history, costs, app health."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from commands.build_log import get_builds, get_all_builds
from commands.fixes_cmd import get_recent_fixes

if TYPE_CHECKING:
    from cost_tracker import CostTracker
    from workspaces import WorkspaceRegistry


def _status_icon(success: bool) -> str:
    return "✅" if success else "❌"


def _platform_icon(platform: str) -> str:
    icons = {"ios": "🍎", "android": "🤖", "web": "🌐"}
    return icons.get(platform.lower(), "📦")


def _format_duration(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def build_dashboard(
    ws_key: str,
    ws_path: str,
    user_id: int,
    cost_tracker: CostTracker,
    registry: WorkspaceRegistry,
    detail: bool = False,
) -> str:
    """Generate the dashboard text for a workspace."""
    sections: list[str] = []
    sections.append(f"📊 **Dashboard — {ws_key}**\n")

    # ── 1. Build History ─────────────────────────────────────────────
    limit = 10 if detail else 5
    builds = get_builds(ws_path, limit=limit)
    if builds:
        sections.append("**Build History**")
        for b in builds:
            icon = _status_icon(b["success"])
            plat = _platform_icon(b["platform"])
            dur = _format_duration(b["duration_secs"])
            ts = b["ts"][5:16].replace("T", " ")  # MM-DD HH:MM
            line = f"  {icon} {plat} {b['platform']:<7} {dur:<8} {ts}"
            if detail and b.get("error"):
                line += f"\n    └ `{b['error'][:80]}`"
            sections.append(line)
    else:
        sections.append("**Build History**\n  No builds yet.")

    # ── 2. Fix Loops ─────────────────────────────────────────────────
    all_builds = get_all_builds(ws_path)
    if all_builds:
        total_builds = len(all_builds)
        successful = sum(1 for b in all_builds if b["success"])
        failed = total_builds - successful
        total_fix_attempts = sum(b.get("attempts", 1) for b in all_builds)
        multi_attempt = [b for b in all_builds if b.get("attempts", 1) > 1]

        sections.append(f"\n**Fix Loops**")
        sections.append(f"  Builds: {total_builds} ({successful} passed, {failed} failed)")
        if total_builds > 0:
            rate = successful / total_builds * 100
            sections.append(f"  Success rate: {rate:.0f}%")
        sections.append(f"  Total build attempts: {total_fix_attempts}")
        if multi_attempt:
            sections.append(f"  Builds needing fixes: {len(multi_attempt)}")
    else:
        sections.append(f"\n**Fix Loops**\n  No data yet.")

    # ── 3. Cost Tracker ──────────────────────────────────────────────
    my_spent = cost_tracker.today_spent(user_id)
    my_tasks = cost_tracker.today_tasks(user_id)
    global_spent = cost_tracker.today_spent()
    global_tasks = cost_tracker.today_tasks()

    # Workspace-level cost from build log
    ws_cost = sum(b.get("cost_usd", 0) for b in all_builds)

    sections.append(f"\n**Cost Tracker**")
    sections.append(f"  Today (you): ${my_spent:.4f} ({my_tasks} tasks)")
    sections.append(f"  Today (global): ${global_spent:.4f} ({global_tasks} tasks)")
    if ws_cost > 0:
        sections.append(f"  Workspace total: ${ws_cost:.4f}")

    # ── 4. App Health ────────────────────────────────────────────────
    sections.append(f"\n**App Health**")
    platform_status: dict[str, str] = {}
    for b in reversed(all_builds):
        plat = b["platform"].lower()
        if plat not in platform_status:
            if b["success"]:
                platform_status[plat] = "✅"
            elif b.get("attempts", 1) > 1:
                platform_status[plat] = "⚠️"
            else:
                platform_status[plat] = "❌"

    for plat in ("ios", "android", "web"):
        icon = _platform_icon(plat)
        status = platform_status.get(plat, "➖")
        sections.append(f"  {icon} {plat.capitalize()}: {status}")

    # ── 5. Workspace Info ────────────────────────────────────────────
    ws_dir = Path(ws_path)
    created = _get_created_date(ws_dir)
    total_iterations = len(all_builds)

    sections.append(f"\n**Workspace Info**")
    sections.append(f"  Path: `{ws_path}`")
    if created:
        sections.append(f"  Created: {created}")
    sections.append(f"  Total iterations: {total_iterations}")

    # Fix log summary in detail mode
    if detail:
        fixes_text = get_recent_fixes(ws_path, max_chars=500)
        if fixes_text:
            sections.append(f"\n**Recent Fixes**\n```\n{fixes_text}\n```")

    return "\n".join(sections)


def _get_created_date(ws_dir: Path) -> str:
    """Best-effort workspace creation date from git or filesystem."""
    git_dir = ws_dir / ".git"
    if git_dir.exists():
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", "--reverse", "--format=%ci", "-1"],
                cwd=ws_dir, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()[:10]
        except Exception:
            pass
    # Fallback: directory creation time
    try:
        import os
        stat = os.stat(ws_dir)
        ts = getattr(stat, "st_birthtime", stat.st_ctime)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return ""
