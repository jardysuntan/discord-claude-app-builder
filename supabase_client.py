"""
supabase_client.py — Async helpers for the Supabase Management API.

Used by /buildapp to auto-provision tables for new apps.
"""

import glob
import os
import re
from typing import Optional

import aiohttp

import config

_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def run_sql(sql: str) -> tuple[bool, str]:
    """Execute SQL via the Supabase Management API.

    Returns (success, error_message_or_empty).
    """
    url = f"https://api.supabase.com/v1/projects/{config.SUPABASE_PROJECT_REF}/database/query"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_MANAGEMENT_KEY}",
        "Content-Type": "application/json",
    }
    body = {"query": sql}

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                if resp.status == 200 or resp.status == 201:
                    return (True, "")
                return (False, f"HTTP {resp.status}: {text[:300]}")
    except aiohttp.ClientError as e:
        return (False, f"Request failed: {e}")
    except Exception as e:
        return (False, f"Unexpected error: {e}")


def extract_sql(text: str) -> Optional[str]:
    """Extract SQL from Claude's markdown response (between ```sql and ``` markers)."""
    match = re.search(r"```sql\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None


# ── SQL guardrails ────────────────────────────────────────────────────────────

# Destructive patterns that should be blocked during auto-sync.
# Each tuple: (compiled regex, human-readable description)
_DESTRUCTIVE_PATTERNS = [
    (re.compile(r"\bDROP\s+TABLE\b(?!\s+IF\s+EXISTS)", re.IGNORECASE),
     "DROP TABLE without IF EXISTS"),
    (re.compile(r"\bDROP\s+SCHEMA\b", re.IGNORECASE),
     "DROP SCHEMA"),
    (re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
     "TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\s+\w+\s*;", re.IGNORECASE),
     "DELETE FROM without WHERE clause"),
]


def check_destructive(sql: str) -> list[str]:
    """Return list of human-readable warnings if SQL contains destructive operations."""
    return [desc for pattern, desc in _DESTRUCTIVE_PATTERNS if pattern.search(sql)]


def patch_idempotent(sql: str) -> str:
    """Add IF NOT EXISTS to CREATE TABLE/INDEX statements that lack it."""
    sql = re.sub(
        r"\bCREATE\s+TABLE\b(?!\s+IF\s+NOT\s+EXISTS)",
        "CREATE TABLE IF NOT EXISTS",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"\bCREATE\s+INDEX\b(?!\s+IF\s+NOT\s+EXISTS)",
        "CREATE INDEX IF NOT EXISTS",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"\bCREATE\s+UNIQUE\s+INDEX\b(?!\s+IF\s+NOT\s+EXISTS)",
        "CREATE UNIQUE INDEX IF NOT EXISTS",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


# ── SQL file auto-sync ────────────────────────────────────────────────────────


def snapshot_sql_files(workspace_path: str) -> dict[str, float]:
    """Glob all *.sql files in the workspace, return {abs_path: mtime}."""
    pattern = os.path.join(workspace_path, "**", "*.sql")
    return {
        p: os.path.getmtime(p)
        for p in glob.glob(pattern, recursive=True)
    }


def _sql_sort_key(path: str) -> tuple[int, str]:
    """Sort SQL files: schema(0) → migrations(1) → functions(3) → seed(5) → other(99)."""
    lower = path.lower()
    if "schema" in lower:
        priority = 0
    elif "migration" in lower:
        priority = 1
    elif "function" in lower:
        priority = 3
    elif "seed" in lower:
        priority = 5
    else:
        priority = 99
    return (priority, path)


def detect_changed_sql(before: dict[str, float], workspace_path: str) -> list[str]:
    """Re-snapshot and return paths where mtime is newer or file is new."""
    after = snapshot_sql_files(workspace_path)
    changed = [
        p for p, mtime in after.items()
        if p not in before or mtime > before[p]
    ]
    changed.sort(key=_sql_sort_key)
    return changed


async def sync_sql_files(changed_files: list[str]) -> tuple[bool, str]:
    """Execute changed SQL files against Supabase sequentially.

    Returns (all_ok, status_message) with per-file results.
    """
    results = []
    all_ok = True

    for path in changed_files:
        name = os.path.basename(path)
        try:
            with open(path, "r") as f:
                sql = f.read().strip()
        except Exception as e:
            results.append(f"❌ {name}: read error — {e}")
            all_ok = False
            continue

        if not sql:
            results.append(f"⏭️ {name}: empty, skipped")
            continue

        # Guardrail: block destructive SQL
        warnings = check_destructive(sql)
        if warnings:
            results.append(f"🛑 {name}: blocked — {', '.join(warnings)}")
            all_ok = False
            continue

        # Guardrail: patch CREATE TABLE/INDEX to be idempotent
        sql = patch_idempotent(sql)

        ok, err = await run_sql(sql)
        if ok:
            results.append(f"✅ {name}")
        else:
            results.append(f"❌ {name}: {err}")
            all_ok = False

    summary = "Database sync: " + ", ".join(results)
    return (all_ok, summary)
