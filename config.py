"""
config.py — Environment loading for KMP cross-platform bot.
Supports Android, iOS, and Web build targets.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_ALLOWED_USER_ID: int = int(os.getenv("DISCORD_ALLOWED_USER_ID", "0"))

DISCORD_ANNOUNCE_CHANNEL_ID: int = int(os.getenv("DISCORD_ANNOUNCE_CHANNEL_ID", "0"))

# ── Workspaces ───────────────────────────────────────────────────────────────
WORKSPACES_PATH: str = os.getenv("WORKSPACES_PATH", "./workspaces.json")
DEFAULT_WORKSPACE: str = os.getenv("DEFAULT_WORKSPACE", "")

# ── Projects & Templates ────────────────────────────────────────────────────
BASE_PROJECTS_DIR: str = os.getenv("BASE_PROJECTS_DIR", os.path.expanduser("~/Projects"))
TEMPLATES_DIR: str = os.getenv("TEMPLATES_DIR", "./templates")

# ── Claude ───────────────────────────────────────────────────────────────────
CLAUDE_BIN: str = os.getenv("CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT: int = int(os.getenv("CLAUDE_TIMEOUT", "180"))

# ── Agent Mode ───────────────────────────────────────────────────────────────
AGENT_MODE: bool = os.getenv("AGENT_MODE", "0") == "1"

# ── Prompt Suggestions ──────────────────────────────────────────────────────
ENABLE_PROMPT_SUGGESTIONS: bool = os.getenv("ENABLE_PROMPT_SUGGESTIONS", "1") == "1"

# ── Android ──────────────────────────────────────────────────────────────────
ADB_BIN: str = os.getenv("ADB_BIN", "adb")
EMULATOR_BIN: str = os.getenv("EMULATOR_BIN", "emulator")
ANDROID_AVD: str = os.getenv("ANDROID_AVD", "")

# ── iOS ──────────────────────────────────────────────────────────────────────
XCODEBUILD: str = os.getenv("XCODEBUILD", "xcodebuild")
XCRUN: str = os.getenv("XCRUN", "xcrun")
IOS_SIMULATOR_NAME: str = os.getenv("IOS_SIMULATOR_NAME", "iPhone 17 Pro Max")
IOS_SIMULATOR_RUNTIME: str = os.getenv("IOS_SIMULATOR_RUNTIME", "iOS-26-2")

# ── App Store Connect (TestFlight) ───────────────────────────────────────────
APPLE_TEAM_ID: str = os.getenv("APPLE_TEAM_ID", "")
ASC_KEY_ID: str = os.getenv("ASC_KEY_ID", "")
ASC_ISSUER_ID: str = os.getenv("ASC_ISSUER_ID", "")
ASC_KEY_PATH: str = os.getenv("ASC_KEY_PATH", "")

# ── Google Play (Internal Testing) ──────────────────────────────────────────
PLAY_JSON_KEY_PATH: str = os.getenv("PLAY_JSON_KEY_PATH", "")
ANDROID_KEYSTORE_PATH: str = os.getenv("ANDROID_KEYSTORE_PATH", "")
ANDROID_KEY_ALIAS: str = os.getenv("ANDROID_KEY_ALIAS", "release-key")
ANDROID_KEYSTORE_PASSWORD: str = os.getenv("ANDROID_KEYSTORE_PASSWORD", "")
ANDROID_KEY_PASSWORD: str = os.getenv("ANDROID_KEY_PASSWORD", "")

# ── Web ──────────────────────────────────────────────────────────────────────
WEB_SERVE_PORT: int = int(os.getenv("WEB_SERVE_PORT", "9000"))
TAILSCALE_HOSTNAME: str = os.getenv("TAILSCALE_HOSTNAME", "")

# ── Cloudflare Pages ─────────────────────────────────────────────────────
CLOUDFLARE_API_TOKEN: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")

# ── KMP Scaffolding ─────────────────────────────────────────────────────────
KMP_PACKAGE_PREFIX: str = os.getenv("KMP_PACKAGE_PREFIX", "com.jaredtan")
TEMPLATE_OLD_PKG: str = os.getenv("TEMPLATE_OLD_PKG", "com.jaredtan.kmptemplate")

# ── Mirror (ws-scrcpy for Android) ──────────────────────────────────────────
SCRCPY_DIR: str = os.getenv("SCRCPY_DIR", os.path.expanduser("~/tools/ws-scrcpy"))
SCRCPY_PORT: int = int(os.getenv("SCRCPY_PORT", "8000"))

# ── Email (for sending build artifacts) ────────────────────────────────────────
GMAIL_ADDRESS: str = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_PROJECT_REF: str = os.getenv("SUPABASE_PROJECT_REF", "")
SUPABASE_MANAGEMENT_KEY: str = os.getenv("SUPABASE_MANAGEMENT_KEY", "")
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")

# ── Queue & Budget ──────────────────────────────────────────────────────
DAILY_TOKEN_CAP_USD: float = float(os.getenv("DAILY_TOKEN_CAP_USD", "50"))
DEFAULT_USER_DAILY_CAP_USD: float = float(os.getenv("DEFAULT_USER_DAILY_CAP_USD", "10"))

# ── Limits ───────────────────────────────────────────────────────────────────
MAX_DISCORD_MSG_LEN: int = 1900
SCREEN_RECORD_SECONDS: int = int(os.getenv("SCREEN_RECORD_SECONDS", "15"))
MAX_BUILD_ATTEMPTS: int = int(os.getenv("MAX_BUILD_ATTEMPTS", "8"))
MAX_FIX_BUDGET_USD: float = float(os.getenv("MAX_FIX_BUDGET_USD", "10"))
SMOKETEST_CHANNEL_ID: int = int(os.getenv("SMOKETEST_CHANNEL_ID", "0"))
MAX_TOTAL_INVOCATIONS: int = int(os.getenv("MAX_TOTAL_INVOCATIONS", "20"))
AUTO_FIX_ON_FAILURE: bool = os.getenv("AUTO_FIX_ON_FAILURE", "1") == "1"


def validate() -> list[str]:
    problems = []
    if not DISCORD_BOT_TOKEN:
        problems.append("DISCORD_BOT_TOKEN is not set")
    if DISCORD_ALLOWED_USER_ID == 0:
        problems.append("DISCORD_ALLOWED_USER_ID is not set")
    if not Path(WORKSPACES_PATH).exists():
        problems.append(f"workspaces.json not found at {WORKSPACES_PATH}")
    return problems


def print_config_summary():
    token_preview = DISCORD_BOT_TOKEN[:8] + "..." if DISCORD_BOT_TOKEN else "(not set)"
    print(f"  Discord token:   {token_preview}")
    print(f"  Allowed user:    {DISCORD_ALLOWED_USER_ID}")
    print(f"  Agent mode:      {'ON' if AGENT_MODE else 'OFF'}")
    print(f"  Claude:          {CLAUDE_BIN} (timeout: {CLAUDE_TIMEOUT}s)")
    print(f"  Android AVD:     {ANDROID_AVD or '(none)'}")
    print(f"  iOS Simulator:   {IOS_SIMULATOR_NAME}")
    print(f"  Web port:        {WEB_SERVE_PORT}")
    print(f"  Tailscale:       {TAILSCALE_HOSTNAME or '(not set)'}")
    print(f"  TestFlight:      {'configured' if APPLE_TEAM_ID and ASC_KEY_ID else 'not configured'}")
    print(f"  Play Store:      {'configured' if PLAY_JSON_KEY_PATH else 'not configured'}")
    print(f"  Supabase:        {'configured' if SUPABASE_PROJECT_REF and SUPABASE_MANAGEMENT_KEY else 'not configured'}")
    print(f"  CF Pages:        {'configured' if CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID else 'not configured'}")
