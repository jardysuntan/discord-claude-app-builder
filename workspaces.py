"""
workspaces.py — Workspace registry backed by workspaces.json.
"""

import json
from pathlib import Path
from typing import Optional

import config


class WorkspaceRegistry:
    def __init__(self):
        self._workspaces: dict[str, str] = {}
        self._user_defaults: dict[int, str] = {}
        self.reload()
        self._global_default = config.DEFAULT_WORKSPACE or None

    def reload(self):
        path = Path(config.WORKSPACES_PATH)
        if path.exists():
            with open(path) as f:
                self._workspaces = json.load(f)
        else:
            self._workspaces = {}

    def list_keys(self) -> list[str]:
        return sorted(self._workspaces.keys())

    def get_path(self, key: str) -> Optional[str]:
        return self._workspaces.get(key.lower())

    def exists(self, key: str) -> bool:
        return key.lower() in self._workspaces

    def add(self, key: str, path: str):
        self._workspaces[key.lower()] = path
        self._save()

    def remove(self, key: str):
        self._workspaces.pop(key.lower(), None)
        self._save()

    def set_default(self, user_id: int, key: str) -> bool:
        if not self.exists(key):
            return False
        self._user_defaults[user_id] = key.lower()
        return True

    def get_default(self, user_id: int) -> Optional[str]:
        return self._user_defaults.get(user_id, self._global_default)

    def resolve(self, key_or_none: Optional[str], user_id: int) -> tuple[Optional[str], Optional[str]]:
        key = key_or_none or self.get_default(user_id)
        if not key:
            return None, None
        path = self.get_path(key)
        return (key, path) if path else (key, None)

    def _save(self):
        with open(config.WORKSPACES_PATH, "w") as f:
            json.dump(self._workspaces, f, indent=2)
