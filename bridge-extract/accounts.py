"""
accounts.py — Multi-tenant account system for Code Gen as a Service.

No Discord dependencies. Accounts are identified by account_id (acc_xxxx),
authenticated via API keys (sk_live_xxxx), and credentials are encrypted at rest.
"""

import hashlib
import json
import os
import secrets
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config


# ── Encryption helpers ──────────────────────────────────────────────────────

_fernet = None


def _get_fernet():
    """Lazy-load Fernet cipher. Key from env or auto-generated file."""
    global _fernet
    if _fernet is not None:
        return _fernet

    from cryptography.fernet import Fernet

    key = config.CREDENTIAL_ENCRYPTION_KEY
    if not key:
        key_file = Path(__file__).parent / ".credential-key"
        if key_file.exists():
            key = key_file.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            key_file.write_text(key + "\n")
            key_file.chmod(0o600)
    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def _encrypt(data: str) -> str:
    return _get_fernet().encrypt(data.encode()).decode()


def _decrypt(data: str) -> str:
    return _get_fernet().decrypt(data.encode()).decode()


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class Account:
    account_id: str
    display_name: str
    email: Optional[str] = None
    role: str = "user"  # "admin" or "user"
    api_keys: list[dict] = field(default_factory=list)
    credentials: dict[str, str] = field(default_factory=dict)  # type -> encrypted JSON
    discord_user_id: Optional[int] = None
    shared_store_access: bool = False
    created_at: str = ""
    daily_cap_usd: float = 10.0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "api_keys": self.api_keys,
            "credentials": self.credentials,
            "discord_user_id": self.discord_user_id,
            "shared_store_access": self.shared_store_access,
            "created_at": self.created_at,
            "daily_cap_usd": self.daily_cap_usd,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        return cls(
            account_id=d["account_id"],
            display_name=d.get("display_name", ""),
            email=d.get("email"),
            role=d.get("role", "user"),
            api_keys=d.get("api_keys", []),
            credentials=d.get("credentials", {}),
            discord_user_id=d.get("discord_user_id"),
            shared_store_access=d.get("shared_store_access", False),
            created_at=d.get("created_at", ""),
            daily_cap_usd=d.get("daily_cap_usd", config.DEFAULT_USER_DAILY_CAP_USD),
        )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _gen_account_id() -> str:
    chars = string.ascii_lowercase + string.digits
    return "acc_" + "".join(secrets.choice(chars) for _ in range(12))


def _gen_api_key() -> str:
    return "sk_live_" + secrets.token_urlsafe(32)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ── Capability definitions ──────────────────────────────────────────────────

CREDENTIAL_TYPES = {"llm", "supabase", "apple", "google"}

_CAPABILITY_MAP = {
    "code_generation": {
        "requires": "llm",
        "unlock": "POST /api/v1/account/credentials/llm",
    },
    "backend": {
        "requires": "supabase",
        "unlock": "POST /api/v1/account/credentials/supabase",
    },
    "publish_ios": {
        "requires": "apple",
        "unlock": "POST /api/v1/account/credentials/apple",
        "alternative": "Request shared store access from admin",
    },
    "publish_android": {
        "requires": "google",
        "unlock": "POST /api/v1/account/credentials/google",
        "alternative": "Request shared store access from admin",
    },
}


# ── AccountManager ──────────────────────────────────────────────────────────

class AccountManager:
    def __init__(self, path: Optional[str] = None):
        self._path = Path(path or config.ACCOUNTS_PATH)
        self._accounts: dict[str, dict] = {}  # account_id -> raw dict
        self._key_index: dict[str, str] = {}  # key_hash -> account_id
        self._discord_index: dict[int, str] = {}  # discord_user_id -> account_id
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                self._accounts = raw
            except (json.JSONDecodeError, OSError):
                self._accounts = {}
        self._rebuild_indexes()

    def _rebuild_indexes(self):
        self._key_index.clear()
        self._discord_index.clear()
        for acct_id, acct in self._accounts.items():
            for key_entry in acct.get("api_keys", []):
                self._key_index[key_entry["key_hash"]] = acct_id
            discord_id = acct.get("discord_user_id")
            if discord_id is not None:
                self._discord_index[int(discord_id)] = acct_id

    def _save(self):
        self._path.write_text(json.dumps(self._accounts, indent=2) + "\n")

    # ── Registration ────────────────────────────────────────────────────

    def register(self, display_name: str, email: Optional[str] = None,
                 role: str = "user") -> tuple[Account, str]:
        """Create a new account with one API key. Returns (account, raw_api_key)."""
        account_id = _gen_account_id()
        raw_key = _gen_api_key()
        key_hash = _hash_key(raw_key)

        acct_data = {
            "account_id": account_id,
            "display_name": display_name,
            "email": email,
            "role": role,
            "api_keys": [{
                "key_hash": key_hash,
                "prefix": raw_key[:8],
                "label": "default",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }],
            "credentials": {},
            "discord_user_id": None,
            "shared_store_access": False,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "daily_cap_usd": config.DEFAULT_USER_DAILY_CAP_USD,
        }
        self._accounts[account_id] = acct_data
        self._key_index[key_hash] = account_id
        self._save()
        return Account.from_dict(acct_data), raw_key

    # ── Authentication ──────────────────────────────────────────────────

    def authenticate(self, raw_api_key: str) -> Optional[Account]:
        """Look up account by raw API key. Returns None if invalid."""
        key_hash = _hash_key(raw_api_key)
        acct_id = self._key_index.get(key_hash)
        if not acct_id:
            return None
        acct_data = self._accounts.get(acct_id)
        if not acct_data:
            return None
        return Account.from_dict(acct_data)

    # ── CRUD ────────────────────────────────────────────────────────────

    def get(self, account_id: str) -> Optional[Account]:
        acct_data = self._accounts.get(account_id)
        return Account.from_dict(acct_data) if acct_data else None

    def get_by_discord_id(self, discord_user_id: int) -> Optional[str]:
        """Return account_id for a Discord user, or None."""
        return self._discord_index.get(discord_user_id)

    def list_accounts(self) -> list[Account]:
        return [Account.from_dict(d) for d in self._accounts.values()]

    # ── API Key management ──────────────────────────────────────────────

    def create_api_key(self, account_id: str, label: str = "default") -> Optional[str]:
        """Create a new API key. Returns the raw key (shown once) or None."""
        acct = self._accounts.get(account_id)
        if not acct:
            return None
        raw_key = _gen_api_key()
        key_hash = _hash_key(raw_key)
        acct.setdefault("api_keys", []).append({
            "key_hash": key_hash,
            "prefix": raw_key[:8],
            "label": label,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self._key_index[key_hash] = account_id
        self._save()
        return raw_key

    def revoke_api_key(self, account_id: str, prefix: str) -> bool:
        """Revoke an API key by its prefix. Returns True if found and removed."""
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        keys = acct.get("api_keys", [])
        for i, entry in enumerate(keys):
            if entry["prefix"] == prefix:
                del keys[i]
                self._key_index.pop(entry["key_hash"], None)
                self._save()
                return True
        return False

    def list_api_keys(self, account_id: str) -> list[dict]:
        """Return API key metadata (prefix, label, created_at) — never the hash."""
        acct = self._accounts.get(account_id)
        if not acct:
            return []
        return [
            {"prefix": k["prefix"], "label": k.get("label", ""), "created_at": k.get("created_at", "")}
            for k in acct.get("api_keys", [])
        ]

    # ── Credential management ───────────────────────────────────────────

    def set_credential(self, account_id: str, cred_type: str, data: dict) -> bool:
        """Encrypt and store a credential. Returns False if account not found."""
        if cred_type not in CREDENTIAL_TYPES:
            return False
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        acct.setdefault("credentials", {})[cred_type] = _encrypt(json.dumps(data))
        self._save()
        return True

    def get_credential(self, account_id: str, cred_type: str) -> Optional[dict]:
        """Decrypt and return a credential, or None."""
        acct = self._accounts.get(account_id)
        if not acct:
            return None
        encrypted = acct.get("credentials", {}).get(cred_type)
        if not encrypted:
            return None
        try:
            return json.loads(_decrypt(encrypted))
        except Exception:
            return None

    def delete_credential(self, account_id: str, cred_type: str) -> bool:
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        creds = acct.get("credentials", {})
        if cred_type in creds:
            del creds[cred_type]
            self._save()
            return True
        return False

    def list_credentials(self, account_id: str) -> dict[str, bool]:
        """Return which credential types are set (no decryption)."""
        acct = self._accounts.get(account_id)
        if not acct:
            return {}
        creds = acct.get("credentials", {})
        return {t: t in creds for t in CREDENTIAL_TYPES}

    # ── Capabilities ────────────────────────────────────────────────────

    def get_capabilities(self, account_id: str) -> dict:
        """Return capabilities dict based on which credentials are set."""
        acct = self._accounts.get(account_id)
        if not acct:
            return {}
        creds = set(acct.get("credentials", {}).keys())
        has_shared = acct.get("shared_store_access", False)

        caps = {}
        for cap_name, cap_def in _CAPABILITY_MAP.items():
            req = cap_def["requires"]
            enabled = req in creds
            # Shared store access can unlock publish capabilities
            if not enabled and has_shared and cap_name in ("publish_ios", "publish_android"):
                enabled = True
            entry = {"enabled": enabled}
            if not enabled:
                entry["unlock"] = cap_def["unlock"]
                if "alternative" in cap_def:
                    entry["alternative"] = cap_def["alternative"]
            caps[cap_name] = entry
        return caps

    def get_setup_checklist(self, account_id: str) -> list[dict]:
        """Return a checklist of setup steps with status and hints."""
        acct = self._accounts.get(account_id)
        if not acct:
            return []
        creds = set(acct.get("credentials", {}).keys())

        items = [
            {
                "step": "Register account",
                "done": True,
                "hint": None,
            },
            {
                "step": "Add LLM API key",
                "done": "llm" in creds,
                "hint": "POST /api/v1/account/credentials/llm with {\"api_key\": \"sk-...\"}",
            },
            {
                "step": "Add Supabase credentials (for backend)",
                "done": "supabase" in creds,
                "hint": "POST /api/v1/account/credentials/supabase with {\"project_ref\": \"...\", \"anon_key\": \"...\", \"management_key\": \"...\"}",
            },
            {
                "step": "Add Apple credentials (for iOS publishing)",
                "done": "apple" in creds or acct.get("shared_store_access", False),
                "hint": "POST /api/v1/account/credentials/apple or request shared store access",
            },
            {
                "step": "Add Google credentials (for Android publishing)",
                "done": "google" in creds or acct.get("shared_store_access", False),
                "hint": "POST /api/v1/account/credentials/google or request shared store access",
            },
        ]
        return items

    # ── Discord linking ─────────────────────────────────────────────────

    def link_discord(self, account_id: str, discord_user_id: int) -> bool:
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        # Remove old link if exists
        old_id = acct.get("discord_user_id")
        if old_id is not None:
            self._discord_index.pop(int(old_id), None)
        acct["discord_user_id"] = discord_user_id
        self._discord_index[discord_user_id] = account_id
        self._save()
        return True

    def unlink_discord(self, account_id: str) -> bool:
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        old_id = acct.get("discord_user_id")
        if old_id is not None:
            self._discord_index.pop(int(old_id), None)
        acct["discord_user_id"] = None
        self._save()
        return True

    # ── Shared store access (admin-only) ────────────────────────────────

    def set_shared_store_access(self, account_id: str, enabled: bool) -> bool:
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        acct["shared_store_access"] = enabled
        self._save()
        return True

    # ── Legacy key registration (for migration) ────────────────────────

    def register_legacy_key(self, account_id: str, raw_key: str, label: str = "legacy") -> bool:
        """Register an existing raw key (e.g. from .api-token) into an account."""
        acct = self._accounts.get(account_id)
        if not acct:
            return False
        key_hash = _hash_key(raw_key)
        acct.setdefault("api_keys", []).append({
            "key_hash": key_hash,
            "prefix": raw_key[:8],
            "label": label,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self._key_index[key_hash] = account_id
        self._save()
        return True
