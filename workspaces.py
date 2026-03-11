"""
workspaces.py — Workspace registry backed by workspaces.json.
Supports per-user ownership with auto-migration from legacy format.
"""

import json
from pathlib import Path
from typing import Optional

import config


class WorkspaceRegistry:
    def __init__(self):
        self._workspaces: dict[str, dict] = {}  # key -> {"path": str, "owner_id": int}
        self._user_defaults: dict[int, str] = {}
        self._user_platforms: dict[int, str] = {}
        self._user_tips_hidden: set[int] = set()
        self._defaults_path = Path(config.WORKSPACES_PATH).parent / "user_defaults.json"
        self._platforms_path = Path(config.WORKSPACES_PATH).parent / "user_platforms.json"
        self._tips_path = Path(config.WORKSPACES_PATH).parent / "user_tips.json"
        self.reload()
        self._global_default = config.DEFAULT_WORKSPACE or None

    def reload(self):
        path = Path(config.WORKSPACES_PATH)
        if path.exists():
            with open(path) as f:
                raw = json.load(f)
            self._workspaces = self._migrate(raw)
        else:
            self._workspaces = {}
        # Load persisted user defaults
        if self._defaults_path.exists():
            try:
                with open(self._defaults_path) as f:
                    raw = json.load(f)
                self._user_defaults = {int(k): v for k, v in raw.items()}
            except (json.JSONDecodeError, ValueError):
                self._user_defaults = {}
        # Load persisted platform preferences
        if self._platforms_path.exists():
            try:
                with open(self._platforms_path) as f:
                    raw = json.load(f)
                self._user_platforms = {int(k): v for k, v in raw.items()}
            except (json.JSONDecodeError, ValueError):
                self._user_platforms = {}
        # Load persisted tips preferences
        if self._tips_path.exists():
            try:
                with open(self._tips_path) as f:
                    raw = json.load(f)
                self._user_tips_hidden = {int(uid) for uid in raw}
            except (json.JSONDecodeError, ValueError):
                self._user_tips_hidden = set()

    def _migrate(self, raw: dict) -> dict[str, dict]:
        """Auto-migrate legacy string values to {path, owner_id} dicts."""
        migrated = False
        result = {}
        for key, value in raw.items():
            if isinstance(value, str):
                # Legacy format: key -> path_string
                result[key] = {"path": value, "owner_id": config.DISCORD_ALLOWED_USER_ID}
                migrated = True
            elif isinstance(value, dict):
                result[key] = value
            else:
                continue  # skip invalid entries
        if migrated:
            self._workspaces = result
            self._save()
        return result

    def list_keys(self, owner_id: Optional[int] = None, user_email: Optional[str] = None) -> list[str]:
        """List workspace keys. If owner_id given, filter to that user's owned + collaborating workspaces."""
        if owner_id is None:
            return sorted(self._workspaces.keys())
        result = []
        for k, v in self._workspaces.items():
            if v.get("owner_id") == owner_id:
                result.append(k)
                continue
            # Check if user is a collaborator (by user_id or email)
            for c in v.get("collaborators", []):
                if c.get("user_id") == owner_id:
                    result.append(k)
                    break
                if user_email and c.get("email", "").lower() == user_email.lower():
                    result.append(k)
                    break
        return sorted(result)

    def can_access(self, key: str, user_id: int, is_admin: bool, user_email: Optional[str] = None) -> bool:
        """Check if user can access a workspace. Admin can access all. Collaborators can access."""
        if is_admin:
            return True
        entry = self._workspaces.get(key.lower())
        if not entry:
            return False
        if entry.get("owner_id") == user_id:
            return True
        for c in entry.get("collaborators", []):
            if c.get("user_id") == user_id:
                return True
            if user_email and c.get("email", "").lower() == user_email.lower():
                return True
        return False

    def is_owner(self, key: str, user_id: int) -> bool:
        """Strict owner check — collaborators do not pass."""
        entry = self._workspaces.get(key.lower())
        if not entry:
            return False
        return entry.get("owner_id") == user_id

    def get_owner(self, key: str) -> Optional[int]:
        entry = self._workspaces.get(key.lower())
        return entry.get("owner_id") if entry else None

    def add_collaborator(self, key: str, name: str, email: str, user_id: Optional[int] = None):
        entry = self._workspaces.get(key.lower())
        if not entry:
            return
        collabs = entry.setdefault("collaborators", [])
        # Don't duplicate by email
        for c in collabs:
            if c.get("email", "").lower() == email.lower():
                if user_id and not c.get("user_id"):
                    c["user_id"] = user_id
                    self._save()
                return
        collabs.append({"name": name, "email": email, "user_id": user_id})
        self._save()

    def remove_collaborator(self, key: str, email: str):
        entry = self._workspaces.get(key.lower())
        if not entry:
            return
        collabs = entry.get("collaborators", [])
        entry["collaborators"] = [c for c in collabs if c.get("email", "").lower() != email.lower()]
        self._save()

    def get_collaborators(self, key: str) -> list[dict]:
        entry = self._workspaces.get(key.lower())
        if not entry:
            return []
        return entry.get("collaborators", [])

    def get_path(self, key: str) -> Optional[str]:
        entry = self._workspaces.get(key.lower())
        if entry is None:
            return None
        return entry.get("path") if isinstance(entry, dict) else entry

    def exists(self, key: str) -> bool:
        return key.lower() in self._workspaces

    def add(self, key: str, path: str, owner_id: Optional[int] = None):
        self._workspaces[key.lower()] = {
            "path": path,
            "owner_id": owner_id or config.DISCORD_ALLOWED_USER_ID,
        }
        self._save()

    def remove(self, key: str):
        self._workspaces.pop(key.lower(), None)
        self._save()

    def rename(self, old_key: str, new_key: str) -> bool:
        """Rename a workspace key. Returns False if old doesn't exist or new already taken."""
        old_key = old_key.lower()
        new_key = new_key.lower()
        if old_key not in self._workspaces or new_key in self._workspaces:
            return False
        entry = self._workspaces.pop(old_key)
        self._workspaces[new_key] = entry
        # Update any user defaults pointing to the old key
        for uid, default in self._user_defaults.items():
            if default == old_key:
                self._user_defaults[uid] = new_key
        if self._global_default == old_key:
            self._global_default = new_key
        self._save()
        self._save_defaults()
        return True

    def set_default(self, user_id: int, key: str) -> bool:
        if not self.exists(key):
            return False
        self._user_defaults[user_id] = key.lower()
        self._save_defaults()
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

    def _save_defaults(self):
        with open(self._defaults_path, "w") as f:
            json.dump({str(k): v for k, v in self._user_defaults.items()}, f, indent=2)

    def set_platform(self, user_id: int, platform: str):
        self._user_platforms[user_id] = platform.lower()
        self._save_platforms()

    def get_platform(self, user_id: int) -> Optional[str]:
        return self._user_platforms.get(user_id)

    def _save_platforms(self):
        with open(self._platforms_path, "w") as f:
            json.dump({str(k): v for k, v in self._user_platforms.items()}, f, indent=2)

    def hide_tips(self, user_id: int):
        self._user_tips_hidden.add(user_id)
        self._save_tips()

    def show_tips(self, user_id: int) -> bool:
        """Returns True if tips should be shown (default)."""
        return user_id not in self._user_tips_hidden

    def _save_tips(self):
        with open(self._tips_path, "w") as f:
            json.dump(list(self._user_tips_hidden), f)
