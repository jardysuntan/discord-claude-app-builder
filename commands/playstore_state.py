"""
commands/playstore_state.py — Per-workspace Play Store setup state.

Persists checklist progress to {workspace_path}/.playstore.json so
users can resume setup across bot restarts and workspace switches.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

STATE_FILENAME = ".playstore.json"


@dataclass
class PlayStoreState:
    developer_account_confirmed: bool = False
    app_created: bool = False                  # user confirmed app created in Play Console
    json_key_path: Optional[str] = None       # path to saved service account .json
    api_access_verified: bool = False          # cached after successful API check
    last_upload_version_code: Optional[int] = None
    last_upload_timestamp: Optional[str] = None
    testers_confirmed: bool = False
    invite_link: Optional[str] = None         # internal testing opt-in URL

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, ws_path: str) -> None:
        """Write state to {ws_path}/.playstore.json."""
        p = Path(ws_path) / STATE_FILENAME
        p.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, ws_path: str) -> "PlayStoreState":
        """Load from disk, or return fresh state if file missing."""
        p = Path(ws_path) / STATE_FILENAME
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return cls()

    @staticmethod
    def exists(ws_path: str) -> bool:
        """Check if workspace has ever started Play Store setup."""
        return (Path(ws_path) / STATE_FILENAME).exists()

    # ── Convenience ──────────────────────────────────────────────────────

    def has_json_key(self) -> bool:
        """Check if stored key path still exists on disk."""
        return bool(self.json_key_path) and Path(self.json_key_path).exists()

    def has_uploaded(self) -> bool:
        """Check if a build was ever uploaded."""
        return self.last_upload_version_code is not None

    def prereqs_met(self) -> bool:
        """Steps 1-4 all done — ready to build & upload."""
        return (
            self.developer_account_confirmed
            and self.app_created
            and self.has_json_key()
            and self.api_access_verified
        )

    def all_done(self) -> bool:
        """All 6 steps complete."""
        return self.prereqs_met() and self.has_uploaded() and self.testers_confirmed
