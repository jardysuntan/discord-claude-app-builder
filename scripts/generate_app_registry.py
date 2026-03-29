#!/usr/bin/env python3
"""
Generate APP_REGISTRY.md — the local source of truth for all apps.

Pulls from workspaces.json + filesystem (git log for last edit) + Supabase schema info.

Usage:
    python scripts/generate_app_registry.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACES_PATH = REPO_ROOT / "workspaces.json"
OUTPUT_PATH = REPO_ROOT / "APP_REGISTRY.md"

# Your Discord user ID
ADMIN_OWNER_ID = 243611843745021952

# Apps that are backfilled into JablueHQ dashboard
DASHBOARD_APPS = {
    "jabluehq",
    "yangzihesobachmobile",
    "novalifepuppy",
    "photoreviewerkmpbased",
}


def get_last_edit(ws_path: str) -> str:
    """Get last git commit date for a workspace, or filesystem mtime as fallback."""
    if not os.path.isdir(ws_path):
        return "(missing)"
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            cwd=ws_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse "2026-03-28 14:30:00 -0700" → "2026-03-28"
            return result.stdout.strip()[:10]
    except Exception:
        pass
    # Fallback: directory mtime
    try:
        ts = os.path.getmtime(ws_path)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def has_supabase_schema(entry: dict) -> bool:
    return bool(entry.get("supabase_schema"))


def main():
    with open(WORKSPACES_PATH) as f:
        workspaces = json.load(f)

    # Separate into categories
    my_apps = []
    other_apps = []
    experiments = []
    smoketests = []

    for slug, entry in sorted(workspaces.items()):
        category = entry.get("category", "app")
        active = entry.get("active", True)
        owner_id = entry.get("owner_id")
        is_mine = owner_id == ADMIN_OWNER_ID
        ws_path = entry.get("path", "")
        last_edit = get_last_edit(ws_path)
        has_db = has_supabase_schema(entry)
        in_dashboard = slug in DASHBOARD_APPS

        row = {
            "slug": slug,
            "path": ws_path,
            "active": active,
            "category": category,
            "is_mine": is_mine,
            "owner_id": owner_id,
            "has_db": has_db,
            "in_dashboard": in_dashboard,
            "last_edit": last_edit,
            "collabs": [c.get("name", c.get("email", "?")) for c in entry.get("collaborators", [])],
        }

        if category == "smoketest":
            smoketests.append(row)
        elif category == "experiment":
            experiments.append(row)
        elif is_mine:
            my_apps.append(row)
        else:
            other_apps.append(row)

    # Generate markdown
    lines = []
    lines.append("# App Registry")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Total workspaces: {len(workspaces)}")
    lines.append("")

    # Stats
    active_count = sum(1 for e in workspaces.values() if e.get("active", True))
    db_count = sum(1 for e in workspaces.values() if has_supabase_schema(e))
    lines.append(f"- Active: {active_count}")
    lines.append(f"- With Supabase DB: {db_count}")
    lines.append(f"- In JablueHQ dashboard: {len(DASHBOARD_APPS)}")
    lines.append(f"- My apps: {len(my_apps)}")
    lines.append(f"- Other users' apps: {len(other_apps)}")
    lines.append(f"- Experiments: {len(experiments)}")
    lines.append(f"- Smoke tests: {len(smoketests)}")
    lines.append("")

    def yes_no(val):
        return "Yes" if val else "-"

    def render_table(title, rows, show_owner=False):
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("*(none)*")
            lines.append("")
            return

        if show_owner:
            lines.append("| Slug | DB | Dashboard | Last Edit | Owner ID | Collabs |")
            lines.append("|------|:--:|:---------:|-----------|----------|---------|")
            for r in rows:
                collabs = ", ".join(r["collabs"]) if r["collabs"] else "-"
                lines.append(
                    f"| `{r['slug']}` "
                    f"| {yes_no(r['has_db'])} "
                    f"| {yes_no(r['in_dashboard'])} "
                    f"| {r['last_edit']} "
                    f"| `{r['owner_id']}` "
                    f"| {collabs} |"
                )
        else:
            lines.append("| Slug | DB | Dashboard | Last Edit | Collabs |")
            lines.append("|------|:--:|:---------:|-----------|---------|")
            for r in rows:
                collabs = ", ".join(r["collabs"]) if r["collabs"] else "-"
                lines.append(
                    f"| `{r['slug']}` "
                    f"| {yes_no(r['has_db'])} "
                    f"| {yes_no(r['in_dashboard'])} "
                    f"| {r['last_edit']} "
                    f"| {collabs} |"
                )
        lines.append("")

    render_table("My Apps (active)", my_apps)
    render_table("Other Users' Apps", other_apps, show_owner=True)
    render_table("Experiments (inactive)", experiments)

    # Smoke tests — compact
    lines.append("## Smoke Tests (inactive)")
    lines.append("")
    if smoketests:
        lines.append(f"{len(smoketests)} smoke test workspaces: " + ", ".join(f"`{r['slug']}`" for r in smoketests))
    else:
        lines.append("*(none)*")
    lines.append("")

    # Legend
    lines.append("---")
    lines.append("")
    lines.append("**Legend:**")
    lines.append("- **DB**: Has a Supabase schema (`supabase_schema` in workspaces.json)")
    lines.append("- **Dashboard**: Appears in the JablueHQ Apps screen")
    lines.append("- **Last Edit**: Last git commit date in the workspace directory")
    lines.append("")
    lines.append("Regenerate: `python scripts/generate_app_registry.py`")

    output = "\n".join(lines) + "\n"
    OUTPUT_PATH.write_text(output)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"\n{output}")


if __name__ == "__main__":
    main()
