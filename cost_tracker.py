"""
cost_tracker.py — Daily spend tracker for Claude API costs.
Supports per-user tracking with auto-migration from legacy format.
Persists to cost_tracker.json in the bot directory.
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional


_DATA_FILE = Path(os.path.dirname(os.path.abspath(__file__))) / "cost_tracker.json"


class CostTracker:
    def __init__(self):
        self._date: str = ""
        self._global: dict = {"spent_usd": 0.0, "tasks": 0}
        self._users: dict[int, dict] = {}  # user_id -> {"spent_usd", "tasks"}
        self._load()

    def _load(self):
        if _DATA_FILE.exists():
            try:
                data = json.loads(_DATA_FILE.read_text())
                self._date = data.get("date", "")
                self._migrate(data)
            except (json.JSONDecodeError, OSError):
                pass
        self._reset_if_new_day()

    def _migrate(self, data: dict):
        """Auto-migrate from legacy flat format to per-user format."""
        if "global" in data:
            # New format
            self._global = data["global"]
            self._users = {int(k): v for k, v in data.get("users", {}).items()}
        else:
            # Legacy format: flat {date, spent_usd, tasks}
            self._global = {
                "spent_usd": data.get("spent_usd", 0.0),
                "tasks": data.get("tasks", 0),
            }
            self._users = {}
            self._save()

    def _save(self):
        _DATA_FILE.write_text(json.dumps({
            "date": self._date,
            "global": {
                "spent_usd": round(self._global["spent_usd"], 6),
                "tasks": self._global["tasks"],
            },
            "users": {
                str(k): {
                    "spent_usd": round(v["spent_usd"], 6),
                    "tasks": v["tasks"],
                }
                for k, v in self._users.items()
            },
        }, indent=2) + "\n")

    def _reset_if_new_day(self):
        today = date.today().isoformat()
        if self._date != today:
            self._date = today
            self._global = {"spent_usd": 0.0, "tasks": 0}
            self._users = {}
            self._save()

    def add(self, cost_usd: float, user_id: Optional[int] = None):
        """Record a cost and increment the task counter."""
        self._reset_if_new_day()
        self._global["spent_usd"] += cost_usd
        self._global["tasks"] += 1
        if user_id is not None:
            if user_id not in self._users:
                self._users[user_id] = {"spent_usd": 0.0, "tasks": 0}
            self._users[user_id]["spent_usd"] += cost_usd
            self._users[user_id]["tasks"] += 1
        self._save()

    def today_spent(self, user_id: Optional[int] = None) -> float:
        """Return total USD spent today, globally or for a specific user."""
        self._reset_if_new_day()
        if user_id is not None:
            return self._users.get(user_id, {}).get("spent_usd", 0.0)
        return self._global["spent_usd"]

    def today_tasks(self, user_id: Optional[int] = None) -> int:
        """Return number of tasks run today."""
        self._reset_if_new_day()
        if user_id is not None:
            return self._users.get(user_id, {}).get("tasks", 0)
        return self._global["tasks"]

    def can_afford(self, cap: float, user_id: Optional[int] = None) -> bool:
        """Return True if spend is below cap for the given user (or global)."""
        self._reset_if_new_day()
        spent = self.today_spent(user_id)
        return spent < cap

    def user_summaries(self) -> list[tuple[int, float, int]]:
        """Return list of (user_id, spent_usd, tasks) for all users today."""
        self._reset_if_new_day()
        return [
            (uid, data["spent_usd"], data["tasks"])
            for uid, data in sorted(self._users.items())
        ]
