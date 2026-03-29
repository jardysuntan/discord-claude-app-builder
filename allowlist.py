"""
allowlist.py — User allowlist backed by allowlist.json.
Controls who can use the bot, their roles, and per-user daily caps.
"""

import json
import os
from pathlib import Path
from typing import Optional

import config


_DATA_FILE = Path(os.path.dirname(os.path.abspath(__file__))) / "allowlist.json"


class Allowlist:
    def __init__(self):
        self._users: dict[int, dict] = {}
        self._load()

    def _load(self):
        if _DATA_FILE.exists():
            try:
                raw = json.loads(_DATA_FILE.read_text())
                self._users = {int(k): v for k, v in raw.items()}
            except (json.JSONDecodeError, OSError, ValueError):
                self._users = {}

        # Auto-seed bootstrap admin from config
        admin_id = config.DISCORD_ALLOWED_USER_ID
        if admin_id and admin_id not in self._users:
            self._users[admin_id] = {
                "role": "admin",
                "display_name": "Owner",
                "daily_cap_usd": config.DAILY_TOKEN_CAP_USD,
            }
            self._save()

    def _save(self):
        _DATA_FILE.write_text(json.dumps(
            {str(k): v for k, v in self._users.items()},
            indent=2,
        ) + "\n")

    def is_allowed(self, user_id: int) -> bool:
        if user_id in self._users:
            return True
        # Check if user has a linked account
        try:
            from accounts import AccountManager
            mgr = AccountManager()
            return mgr.get_by_discord_id(user_id) is not None
        except Exception:
            return False

    def is_admin(self, user_id: int) -> bool:
        if self._users.get(user_id, {}).get("role") == "admin":
            return True
        # Check account role if linked
        try:
            from accounts import AccountManager
            mgr = AccountManager()
            acct_id = mgr.get_by_discord_id(user_id)
            if acct_id:
                acct = mgr.get(acct_id)
                return acct is not None and acct.role == "admin"
        except Exception:
            pass
        return False

    def add(self, user_id: int, display_name: str, daily_cap_usd: Optional[float] = None, email: Optional[str] = None):
        if user_id in self._users:
            # Link email if not set yet
            if email and not self._users[user_id].get("email"):
                self._users[user_id]["email"] = email
                self._save()
            return
        entry = {
            "role": "user",
            "display_name": display_name,
            "daily_cap_usd": daily_cap_usd or config.DEFAULT_USER_DAILY_CAP_USD,
        }
        if email:
            entry["email"] = email
        self._users[user_id] = entry
        self._save()

        # Auto-create an account when a Discord user is added
        try:
            from accounts import AccountManager
            mgr = AccountManager()
            if not mgr.get_by_discord_id(user_id):
                acct, _ = mgr.register(display_name, email=email)
                mgr.link_discord(acct.account_id, user_id)
        except Exception:
            pass  # account creation is best-effort

    def set_email(self, user_id: int, email: str) -> bool:
        if user_id not in self._users:
            return False
        self._users[user_id]["email"] = email
        self._save()
        return True

    def get_email(self, user_id: int) -> Optional[str]:
        entry = self._users.get(user_id)
        return entry.get("email") if entry else None

    def find_by_email(self, email: str) -> Optional[int]:
        """Find user ID by email. Returns None if not found."""
        for uid, info in self._users.items():
            if info.get("email", "").lower() == email.lower():
                return uid
        return None

    def add_pending_invite(self, email: str):
        """Store an email for someone who hasn't joined Discord yet."""
        # Store in a special key
        pending = self._users.get(0, {}).get("pending_invites", [])
        if email.lower() not in [p.lower() for p in pending]:
            pending.append(email)
            if 0 not in self._users:
                self._users[0] = {"pending_invites": pending}
            else:
                self._users[0]["pending_invites"] = pending
            self._save()

    def claim_pending_invite(self, email: str) -> bool:
        """Check if email was pre-invited. Returns True and removes from pending."""
        pending = self._users.get(0, {}).get("pending_invites", [])
        lower_pending = [p.lower() for p in pending]
        if email.lower() in lower_pending:
            idx = lower_pending.index(email.lower())
            pending.pop(idx)
            if pending:
                self._users[0]["pending_invites"] = pending
            else:
                self._users.pop(0, None)
            self._save()
            return True
        return False

    def get_pending_invites(self) -> list[str]:
        return self._users.get(0, {}).get("pending_invites", [])

    def remove(self, user_id: int) -> bool:
        """Remove a user. Returns False if they are the bootstrap admin."""
        if user_id == config.DISCORD_ALLOWED_USER_ID:
            return False
        if user_id in self._users:
            del self._users[user_id]
            self._save()
            return True
        return False

    def get_daily_cap(self, user_id: int) -> float:
        entry = self._users.get(user_id)
        if entry:
            return entry.get("daily_cap_usd", config.DEFAULT_USER_DAILY_CAP_USD)
        return config.DEFAULT_USER_DAILY_CAP_USD

    def set_daily_cap(self, user_id: int, cap_usd: float) -> bool:
        if user_id not in self._users:
            return False
        self._users[user_id]["daily_cap_usd"] = cap_usd
        self._save()
        return True

    def get_display_name(self, user_id: int) -> Optional[str]:
        entry = self._users.get(user_id)
        return entry.get("display_name") if entry else None

    def list_users(self) -> list[tuple[int, dict]]:
        """Return list of (user_id, info_dict) sorted by role (admin first)."""
        return sorted(
            self._users.items(),
            key=lambda x: (0 if x[1].get("role") == "admin" else 1, x[0]),
        )

    def reload(self):
        self._load()
