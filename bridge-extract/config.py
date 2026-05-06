"""
config.py — Slim env shim for the standalone extract service.

Mirrors the names from the parent bridge's config.py for the values that
accounts.py touches, so accounts.py stays identical and diffable against
the parent.
"""
import os

ACCOUNTS_PATH: str = os.getenv("ACCOUNTS_PATH", "/data/accounts.json")
CREDENTIAL_ENCRYPTION_KEY: str = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
DEFAULT_USER_DAILY_CAP_USD: float = float(os.getenv("DEFAULT_USER_DAILY_CAP_USD", "10"))
