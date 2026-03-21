"""commands/build_log.py — Persistent build history per workspace."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def _log_path(ws_path: str) -> Path:
    return Path(ws_path) / ".builds.json"


def log_build(
    ws_path: str,
    platform: str,
    success: bool,
    duration_secs: float,
    attempts: int = 1,
    cost_usd: float = 0.0,
    error: str = "",
) -> None:
    """Append a build result to the workspace build log."""
    p = _log_path(ws_path)
    entries = _load(p)
    entries.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "platform": platform,
        "success": success,
        "duration_secs": round(duration_secs, 1),
        "attempts": attempts,
        "cost_usd": round(cost_usd, 4),
        "error": error[:300] if error else "",
    })
    # Keep last 50 entries
    if len(entries) > 50:
        entries = entries[-50:]
    p.write_text(json.dumps(entries, indent=2) + "\n")


def get_builds(ws_path: str, limit: int = 10) -> list[dict]:
    """Return recent build entries, newest first."""
    entries = _load(_log_path(ws_path))
    return list(reversed(entries[-limit:]))


def get_all_builds(ws_path: str) -> list[dict]:
    """Return all build entries."""
    return _load(_log_path(ws_path))


def _load(p: Path) -> list[dict]:
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
