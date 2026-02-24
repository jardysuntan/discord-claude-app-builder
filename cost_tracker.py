"""
cost_tracker.py — Daily spend tracker for Claude API costs.
Persists to cost_tracker.json in the bot directory.
"""

import json
import os
from datetime import date
from pathlib import Path


_DATA_FILE = Path(os.path.dirname(os.path.abspath(__file__))) / "cost_tracker.json"


class CostTracker:
    def __init__(self):
        self._date: str = ""
        self._spent_usd: float = 0.0
        self._tasks: int = 0
        self._load()

    def _load(self):
        if _DATA_FILE.exists():
            try:
                data = json.loads(_DATA_FILE.read_text())
                self._date = data.get("date", "")
                self._spent_usd = data.get("spent_usd", 0.0)
                self._tasks = data.get("tasks", 0)
            except (json.JSONDecodeError, OSError):
                pass
        self._reset_if_new_day()

    def _save(self):
        _DATA_FILE.write_text(json.dumps({
            "date": self._date,
            "spent_usd": round(self._spent_usd, 6),
            "tasks": self._tasks,
        }, indent=2) + "\n")

    def _reset_if_new_day(self):
        today = date.today().isoformat()
        if self._date != today:
            self._date = today
            self._spent_usd = 0.0
            self._tasks = 0
            self._save()

    def add(self, cost_usd: float):
        """Record a cost and increment the task counter."""
        self._reset_if_new_day()
        self._spent_usd += cost_usd
        self._tasks += 1
        self._save()

    def today_spent(self) -> float:
        """Return total USD spent today."""
        self._reset_if_new_day()
        return self._spent_usd

    def today_tasks(self) -> int:
        """Return number of tasks run today."""
        self._reset_if_new_day()
        return self._tasks

    def can_afford(self, cap: float, pct: float = 0.9) -> bool:
        """Return True if today's spend is below pct% of cap."""
        self._reset_if_new_day()
        return self._spent_usd < cap * pct
