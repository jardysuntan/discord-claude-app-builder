"""
extract_limits.py — Per-account daily quota for the LLM extract endpoints.

Why this exists: when the bridge is publicly reachable (e.g. via Tailscale
Funnel) we don't want a single account to burn through the admin BYOK keys
in a runaway loop. Cap = 1 successful extract per account per UTC day, on
both /api/v1/extract and /api/v1/extract-doc-text combined.

State file: extract_limits.json (gitignored), JSON shape:
    {
      "<account_id>": {
        "date": "2026-05-04",     # UTC date of the last counted call
        "count": 1                # extracts used today
      },
      ...
    }

Admin accounts (role == "admin") bypass the limit so the dev/E2E loop and
admin BYOK testing keep working without surprises.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DAILY_LIMIT = int(os.getenv("EXTRACT_DAILY_LIMIT", "1"))
GLOBAL_DAILY_CAP = int(os.getenv("EXTRACT_GLOBAL_DAILY_CAP", "100"))
# Per-creator cap. All app users share the same bridge account_id (single
# BridgeConfig.TOKEN), so the per-account cap above is effectively a
# whole-app cap. The per-creator cap binds the extract spend to whichever
# individual user (Supabase auth_id, or anon guest_id when not signed in)
# initiated the request — protects the shared default LLM key from a
# single user looping the import button.
CREATOR_DAILY_LIMIT = int(os.getenv("EXTRACT_CREATOR_DAILY_LIMIT", "5"))

_path = Path(os.getenv("EXTRACT_LIMITS_PATH", "./extract_limits.json"))
_lock = threading.Lock()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load() -> dict:
    if not _path.exists():
        return {}
    try:
        return json.loads(_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    _path.write_text(json.dumps(data, indent=2) + "\n")


def check_and_consume(
    account_id: str,
    *,
    is_admin: bool = False,
    creator_id: str | None = None,
) -> tuple[bool, int, int]:
    """
    Try to charge one extract against today's quota.

    Three nested caps, checked in this order:
      1. Global daily cap (GLOBAL_DAILY_CAP) — protects the shared LLM key
         from runaway spend across all users combined.
      2. Per-creator daily cap (CREATOR_DAILY_LIMIT) — when `creator_id` is
         supplied, caps how many extracts a single user can run today.
         Skipped if creator_id is None (legacy callers).
      3. Per-account daily cap (DAILY_LIMIT) — historically the only cap.
         All app users share one account_id today, so this acts like a
         second global limit.

    Returns (allowed, used_today, limit). When allowed=False, `used_today`
    is the count from whichever cap fired, and `limit` is that cap's value.
    When allowed=True, counts are incremented for global, account, and (if
    supplied) creator. Admin accounts always pass and do NOT consume quota.
    """
    if is_admin:
        return True, 0, DAILY_LIMIT

    today = _today()
    with _lock:
        data = _load()

        # 1. Global cap.
        global_entry = data.get("__global__") or {}
        if global_entry.get("date") != today:
            global_entry = {"date": today, "count": 0}
        global_used = int(global_entry.get("count", 0))
        if global_used >= GLOBAL_DAILY_CAP:
            return False, global_used, GLOBAL_DAILY_CAP

        # 2. Per-creator cap. Keyed under "creator:<id>" so it can't collide
        # with an account_id of the same shape.
        creator_key = f"creator:{creator_id}" if creator_id else None
        creator_entry = None
        if creator_key:
            creator_entry = data.get(creator_key) or {}
            if creator_entry.get("date") != today:
                creator_entry = {"date": today, "count": 0}
            creator_used = int(creator_entry.get("count", 0))
            if creator_used >= CREATOR_DAILY_LIMIT:
                return False, creator_used, CREATOR_DAILY_LIMIT

        # 3. Per-account cap.
        entry = data.get(account_id) or {}
        if entry.get("date") != today:
            entry = {"date": today, "count": 0}
        used = int(entry.get("count", 0))
        if used >= DAILY_LIMIT:
            return False, used, DAILY_LIMIT

        # All caps passed — increment everything we tracked.
        entry["count"] = used + 1
        global_entry["count"] = global_used + 1
        data[account_id] = entry
        data["__global__"] = global_entry
        if creator_entry is not None and creator_key is not None:
            creator_entry["count"] = int(creator_entry.get("count", 0)) + 1
            data[creator_key] = creator_entry
        _save(data)
        return True, entry["count"], DAILY_LIMIT


def status(account_id: str) -> dict:
    """Inspect (don't consume) today's usage for an account."""
    today = _today()
    data = _load()
    entry = data.get(account_id) or {}
    if entry.get("date") != today:
        return {"date": today, "count": 0, "limit": DAILY_LIMIT}
    return {
        "date": today,
        "count": int(entry.get("count", 0)),
        "limit": DAILY_LIMIT,
    }
