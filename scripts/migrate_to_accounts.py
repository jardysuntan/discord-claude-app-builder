#!/usr/bin/env python3
"""
migrate_to_accounts.py — One-time migration from single-user to multi-tenant.

1. Reads allowlist.json → creates an account per user in accounts.json
2. Maps existing .api-token as a legacy key on the admin account
3. Copies admin's Supabase/Apple/Google creds from env vars into the account
4. Updates workspaces.json with account_id fields
5. Updates user_defaults.json and user_platforms.json to include account_id keys
6. Backs up all files as *.pre-migration.bak before modifying
"""

import json
import os
import shutil
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from accounts import AccountManager


def backup(path: Path):
    if path.exists():
        bak = path.with_suffix(path.suffix + ".pre-migration.bak")
        shutil.copy2(path, bak)
        print(f"  Backed up {path.name} → {bak.name}")


def main():
    print("=== Multi-Tenant Account Migration ===\n")

    allowlist_path = Path(os.path.dirname(os.path.abspath(config.__file__))) / "allowlist.json"
    workspaces_path = Path(config.WORKSPACES_PATH)
    accounts_path = Path(config.ACCOUNTS_PATH)
    defaults_path = workspaces_path.parent / "user_defaults.json"
    platforms_path = workspaces_path.parent / "user_platforms.json"

    if accounts_path.exists():
        print(f"⚠️  {accounts_path} already exists. Aborting to avoid overwrite.")
        print("   Delete it first if you want to re-run the migration.")
        sys.exit(1)

    # Backup all files
    print("1. Backing up files...")
    for p in [allowlist_path, workspaces_path, defaults_path, platforms_path]:
        backup(p)

    # Read allowlist
    print("\n2. Reading allowlist...")
    allowlist_data = {}
    if allowlist_path.exists():
        allowlist_data = json.loads(allowlist_path.read_text())
    else:
        print("   No allowlist.json found, creating admin-only account.")

    # Initialize AccountManager (creates empty accounts.json)
    mgr = AccountManager(str(accounts_path))

    # Track discord_user_id -> account_id mapping
    discord_to_account: dict[int, str] = {}
    generated_keys: list[tuple[str, str, str]] = []  # (display_name, account_id, raw_key)

    admin_account_id = None

    print("\n3. Creating accounts from allowlist...")
    for uid_str, info in allowlist_data.items():
        uid = int(uid_str)
        if uid == 0:
            continue  # skip pending_invites entry

        display_name = info.get("display_name", f"User {uid}")
        role = info.get("role", "user")
        email = info.get("email")

        acct, raw_key = mgr.register(display_name, email=email, role=role)
        mgr.link_discord(acct.account_id, uid)
        acct.daily_cap_usd = info.get("daily_cap_usd", config.DEFAULT_USER_DAILY_CAP_USD)
        # Update daily_cap in stored data
        mgr._accounts[acct.account_id]["daily_cap_usd"] = acct.daily_cap_usd
        mgr._save()

        discord_to_account[uid] = acct.account_id
        generated_keys.append((display_name, acct.account_id, raw_key))

        if role == "admin" and admin_account_id is None:
            admin_account_id = acct.account_id

        print(f"   Created: {display_name} ({role}) → {acct.account_id}")

    # If no admin found from allowlist, create one from config
    if admin_account_id is None and config.DISCORD_ALLOWED_USER_ID:
        acct, raw_key = mgr.register("Owner", role="admin")
        mgr.link_discord(acct.account_id, config.DISCORD_ALLOWED_USER_ID)
        admin_account_id = acct.account_id
        discord_to_account[config.DISCORD_ALLOWED_USER_ID] = acct.account_id
        generated_keys.append(("Owner", acct.account_id, raw_key))
        print(f"   Created admin from config: {acct.account_id}")

    # Register legacy .api-token on admin account
    print("\n4. Registering legacy API token...")
    token_file = Path(__file__).resolve().parent.parent / ".api-token"
    if token_file.exists() and admin_account_id:
        legacy_token = token_file.read_text().strip()
        mgr.register_legacy_key(admin_account_id, legacy_token, label="legacy-api-token")
        print(f"   Registered .api-token on admin account {admin_account_id}")
    else:
        print("   No .api-token file found or no admin account. Skipping.")

    # Copy admin credentials from env vars
    print("\n5. Storing admin credentials from env vars...")
    if admin_account_id:
        if config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
            mgr.set_credential(admin_account_id, "supabase", {
                "project_ref": config.SUPABASE_PROJECT_REF,
                "management_key": config.SUPABASE_MANAGEMENT_KEY,
                "anon_key": config.SUPABASE_ANON_KEY,
            })
            print("   Stored Supabase credentials")

        if config.APPLE_TEAM_ID and config.ASC_KEY_ID:
            mgr.set_credential(admin_account_id, "apple", {
                "team_id": config.APPLE_TEAM_ID,
                "asc_key_id": config.ASC_KEY_ID,
                "asc_issuer_id": config.ASC_ISSUER_ID,
                "asc_key_path": config.ASC_KEY_PATH,
            })
            print("   Stored Apple credentials")

        if config.PLAY_JSON_KEY_PATH:
            mgr.set_credential(admin_account_id, "google", {
                "play_json_key_path": config.PLAY_JSON_KEY_PATH,
                "keystore_path": config.ANDROID_KEYSTORE_PATH,
                "key_alias": config.ANDROID_KEY_ALIAS,
                "keystore_password": config.ANDROID_KEYSTORE_PASSWORD,
                "key_password": config.ANDROID_KEY_PASSWORD,
            })
            print("   Stored Google Play credentials")

    # Update workspaces.json with account_id
    print("\n6. Updating workspaces.json with account_id...")
    if workspaces_path.exists():
        workspaces = json.loads(workspaces_path.read_text())
        updated = 0
        for slug, entry in workspaces.items():
            if not isinstance(entry, dict):
                continue
            owner_id = entry.get("owner_id")
            if owner_id and int(owner_id) in discord_to_account:
                entry["account_id"] = discord_to_account[int(owner_id)]
                updated += 1
        workspaces_path.write_text(json.dumps(workspaces, indent=2) + "\n")
        print(f"   Updated {updated} workspaces")

    # Update user_defaults.json
    print("\n7. Updating user_defaults.json...")
    if defaults_path.exists():
        defaults = json.loads(defaults_path.read_text())
        new_defaults = {}
        for uid_str, ws_slug in defaults.items():
            uid = int(uid_str)
            new_defaults[uid_str] = ws_slug
            if uid in discord_to_account:
                new_defaults[discord_to_account[uid]] = ws_slug
        defaults_path.write_text(json.dumps(new_defaults, indent=2) + "\n")
        print(f"   Added account_id keys to defaults")

    # Update user_platforms.json
    print("\n8. Updating user_platforms.json...")
    if platforms_path.exists():
        platforms = json.loads(platforms_path.read_text())
        new_platforms = {}
        for uid_str, platform in platforms.items():
            uid = int(uid_str)
            new_platforms[uid_str] = platform
            if uid in discord_to_account:
                new_platforms[discord_to_account[uid]] = platform
        platforms_path.write_text(json.dumps(new_platforms, indent=2) + "\n")
        print(f"   Added account_id keys to platforms")

    # Print generated keys
    print("\n" + "=" * 60)
    print("GENERATED API KEYS (save these — shown ONCE)")
    print("=" * 60)
    for display_name, acct_id, raw_key in generated_keys:
        print(f"\n  {display_name} ({acct_id}):")
        print(f"    {raw_key}")
    print("\n" + "=" * 60)
    print("\nMigration complete!")


if __name__ == "__main__":
    main()
