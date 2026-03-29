"""Tests for the multi-tenant account system (accounts.py).

Covers: registration, authentication, API key management, credential
encryption/decryption, capabilities computation, setup checklist,
Discord linking, shared store access, and workspace scoping.
"""

import json
import os
import tempfile

from accounts import AccountManager, Account, _hash_key, _gen_api_key


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fresh_mgr() -> tuple[AccountManager, str]:
    """Create an AccountManager backed by a temp file. Returns (mgr, path)."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)  # AccountManager creates it fresh
    return AccountManager(path), path


def _cleanup(path: str):
    for p in (path,):
        if os.path.exists(p):
            os.unlink(p)


# ── Registration ────────────────────────────────────────────────────────────

def test_register_creates_account():
    mgr, path = _fresh_mgr()
    try:
        acct, key = mgr.register("Alice")
        assert acct.account_id.startswith("acc_")
        assert len(acct.account_id) == 16  # "acc_" + 12 chars
        assert acct.display_name == "Alice"
        assert acct.role == "user"
        assert key.startswith("sk_live_")
        assert acct.created_at != ""
    finally:
        _cleanup(path)


def test_register_with_email_and_role():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Bob", email="bob@test.com", role="admin")
        assert acct.email == "bob@test.com"
        assert acct.role == "admin"
    finally:
        _cleanup(path)


def test_register_persists_to_disk():
    mgr, path = _fresh_mgr()
    try:
        acct, key = mgr.register("Charlie")
        # Read back from disk
        mgr2 = AccountManager(path)
        loaded = mgr2.get(acct.account_id)
        assert loaded is not None
        assert loaded.display_name == "Charlie"
        # Authenticate with the new manager instance
        authed = mgr2.authenticate(key)
        assert authed is not None
        assert authed.account_id == acct.account_id
    finally:
        _cleanup(path)


# ── Authentication ──────────────────────────────────────────────────────────

def test_authenticate_valid_key():
    mgr, path = _fresh_mgr()
    try:
        acct, key = mgr.register("Diana")
        result = mgr.authenticate(key)
        assert result is not None
        assert result.account_id == acct.account_id
    finally:
        _cleanup(path)


def test_authenticate_invalid_key():
    mgr, path = _fresh_mgr()
    try:
        mgr.register("Eve")
        result = mgr.authenticate("sk_live_totally_wrong_key_here")
        assert result is None
    finally:
        _cleanup(path)


def test_authenticate_empty_string():
    mgr, path = _fresh_mgr()
    try:
        mgr.register("Frank")
        result = mgr.authenticate("")
        assert result is None
    finally:
        _cleanup(path)


# ── API Key Management ──────────────────────────────────────────────────────

def test_create_second_api_key():
    mgr, path = _fresh_mgr()
    try:
        acct, key1 = mgr.register("Grace")
        key2 = mgr.create_api_key(acct.account_id, "secondary")
        assert key2 is not None
        assert key2.startswith("sk_live_")
        assert key2 != key1
        # Both keys should authenticate to the same account
        assert mgr.authenticate(key1).account_id == acct.account_id
        assert mgr.authenticate(key2).account_id == acct.account_id
    finally:
        _cleanup(path)


def test_revoke_api_key():
    mgr, path = _fresh_mgr()
    try:
        acct, key = mgr.register("Heidi")
        prefix = key[:8]
        ok = mgr.revoke_api_key(acct.account_id, prefix)
        assert ok is True
        # Key should no longer work
        assert mgr.authenticate(key) is None
    finally:
        _cleanup(path)


def test_revoke_nonexistent_key():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Ivan")
        ok = mgr.revoke_api_key(acct.account_id, "xx_fake_")
        assert ok is False
    finally:
        _cleanup(path)


def test_list_api_keys():
    mgr, path = _fresh_mgr()
    try:
        acct, key = mgr.register("Judy")
        mgr.create_api_key(acct.account_id, "backup")
        keys = mgr.list_api_keys(acct.account_id)
        assert len(keys) == 2
        assert keys[0]["label"] == "default"
        assert keys[1]["label"] == "backup"
        # Keys should have prefix but never the hash
        assert keys[0]["prefix"] == key[:8]
        assert "key_hash" not in keys[0]
    finally:
        _cleanup(path)


def test_list_api_keys_nonexistent_account():
    mgr, path = _fresh_mgr()
    try:
        keys = mgr.list_api_keys("acc_doesnotexist")
        assert keys == []
    finally:
        _cleanup(path)


# ── Credential Management ──────────────────────────────────────────────────

def test_set_and_get_credential():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Karl")
        ok = mgr.set_credential(acct.account_id, "llm", {"api_key": "sk-test123", "model": "gpt-4"})
        assert ok is True
        cred = mgr.get_credential(acct.account_id, "llm")
        assert cred == {"api_key": "sk-test123", "model": "gpt-4"}
    finally:
        _cleanup(path)


def test_credential_encrypted_at_rest():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Liam")
        mgr.set_credential(acct.account_id, "supabase", {"project_ref": "xyzabc"})
        # Read raw JSON from disk
        raw = json.loads(open(path).read())
        encrypted_value = raw[acct.account_id]["credentials"]["supabase"]
        # Should NOT contain the plaintext
        assert "xyzabc" not in encrypted_value
        # But decrypted should match
        decrypted = mgr.get_credential(acct.account_id, "supabase")
        assert decrypted["project_ref"] == "xyzabc"
    finally:
        _cleanup(path)


def test_credential_persists_across_reload():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Mia")
        mgr.set_credential(acct.account_id, "apple", {"team_id": "ABCDEF"})
        # Reload from disk
        mgr2 = AccountManager(path)
        cred = mgr2.get_credential(acct.account_id, "apple")
        assert cred == {"team_id": "ABCDEF"}
    finally:
        _cleanup(path)


def test_delete_credential():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Nora")
        mgr.set_credential(acct.account_id, "google", {"key_path": "/tmp/key.json"})
        assert mgr.get_credential(acct.account_id, "google") is not None
        ok = mgr.delete_credential(acct.account_id, "google")
        assert ok is True
        assert mgr.get_credential(acct.account_id, "google") is None
    finally:
        _cleanup(path)


def test_delete_nonexistent_credential():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Oscar")
        ok = mgr.delete_credential(acct.account_id, "llm")
        assert ok is False
    finally:
        _cleanup(path)


def test_invalid_credential_type():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Pat")
        ok = mgr.set_credential(acct.account_id, "invalid_type", {"foo": "bar"})
        assert ok is False
    finally:
        _cleanup(path)


def test_list_credentials():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Quinn")
        mgr.set_credential(acct.account_id, "llm", {"key": "test"})
        mgr.set_credential(acct.account_id, "supabase", {"ref": "test"})
        creds = mgr.list_credentials(acct.account_id)
        assert creds["llm"] is True
        assert creds["supabase"] is True
        assert creds["apple"] is False
        assert creds["google"] is False
    finally:
        _cleanup(path)


# ── Capabilities ────────────────────────────────────────────────────────────

def test_capabilities_empty_account():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Rita")
        caps = mgr.get_capabilities(acct.account_id)
        assert caps["code_generation"]["enabled"] is False
        assert caps["backend"]["enabled"] is False
        assert caps["publish_ios"]["enabled"] is False
        assert caps["publish_android"]["enabled"] is False
        # Should have unlock hints
        assert "unlock" in caps["code_generation"]
        assert "unlock" in caps["publish_ios"]
        assert "alternative" in caps["publish_ios"]
    finally:
        _cleanup(path)


def test_capabilities_with_llm():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Sam")
        mgr.set_credential(acct.account_id, "llm", {"key": "test"})
        caps = mgr.get_capabilities(acct.account_id)
        assert caps["code_generation"]["enabled"] is True
        assert "unlock" not in caps["code_generation"]
        assert caps["backend"]["enabled"] is False
    finally:
        _cleanup(path)


def test_capabilities_with_all_credentials():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Tina")
        for ctype in ("llm", "supabase", "apple", "google"):
            mgr.set_credential(acct.account_id, ctype, {"key": "test"})
        caps = mgr.get_capabilities(acct.account_id)
        assert all(caps[c]["enabled"] for c in caps)
    finally:
        _cleanup(path)


def test_capabilities_shared_store_unlocks_publish():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Uma")
        mgr.set_shared_store_access(acct.account_id, True)
        caps = mgr.get_capabilities(acct.account_id)
        assert caps["publish_ios"]["enabled"] is True
        assert caps["publish_android"]["enabled"] is True
        # But code gen still needs LLM key
        assert caps["code_generation"]["enabled"] is False
    finally:
        _cleanup(path)


def test_capabilities_revoked_after_credential_delete():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Vera")
        mgr.set_credential(acct.account_id, "llm", {"key": "test"})
        assert mgr.get_capabilities(acct.account_id)["code_generation"]["enabled"] is True
        mgr.delete_credential(acct.account_id, "llm")
        assert mgr.get_capabilities(acct.account_id)["code_generation"]["enabled"] is False
    finally:
        _cleanup(path)


# ── Setup Checklist ─────────────────────────────────────────────────────────

def test_setup_checklist_fresh_account():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Wendy")
        checklist = mgr.get_setup_checklist(acct.account_id)
        assert len(checklist) == 5
        # Only "Register account" should be done
        done_steps = [c["step"] for c in checklist if c["done"]]
        assert done_steps == ["Register account"]
        # All undone steps should have hints
        for c in checklist:
            if not c["done"]:
                assert c["hint"] is not None
    finally:
        _cleanup(path)


def test_setup_checklist_with_llm():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Xena")
        mgr.set_credential(acct.account_id, "llm", {"key": "test"})
        checklist = mgr.get_setup_checklist(acct.account_id)
        done_steps = [c["step"] for c in checklist if c["done"]]
        assert "Register account" in done_steps
        assert "Add LLM API key" in done_steps
    finally:
        _cleanup(path)


def test_setup_checklist_shared_store_marks_publish():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Yara")
        mgr.set_shared_store_access(acct.account_id, True)
        checklist = mgr.get_setup_checklist(acct.account_id)
        done_steps = [c["step"] for c in checklist if c["done"]]
        assert "Add Apple credentials (for iOS publishing)" in done_steps
        assert "Add Google credentials (for Android publishing)" in done_steps
    finally:
        _cleanup(path)


# ── Discord Linking ─────────────────────────────────────────────────────────

def test_link_discord():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Zara")
        ok = mgr.link_discord(acct.account_id, 123456789)
        assert ok is True
        found = mgr.get_by_discord_id(123456789)
        assert found == acct.account_id
    finally:
        _cleanup(path)


def test_link_discord_persists():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Amy")
        mgr.link_discord(acct.account_id, 987654321)
        # Reload
        mgr2 = AccountManager(path)
        assert mgr2.get_by_discord_id(987654321) == acct.account_id
    finally:
        _cleanup(path)


def test_unlink_discord():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Beth")
        mgr.link_discord(acct.account_id, 111222333)
        ok = mgr.unlink_discord(acct.account_id)
        assert ok is True
        assert mgr.get_by_discord_id(111222333) is None
    finally:
        _cleanup(path)


def test_link_discord_replaces_old_link():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Cass")
        mgr.link_discord(acct.account_id, 100)
        mgr.link_discord(acct.account_id, 200)
        assert mgr.get_by_discord_id(100) is None
        assert mgr.get_by_discord_id(200) == acct.account_id
    finally:
        _cleanup(path)


def test_get_by_discord_id_not_found():
    mgr, path = _fresh_mgr()
    try:
        mgr.register("Dana")
        assert mgr.get_by_discord_id(999999) is None
    finally:
        _cleanup(path)


# ── Shared Store Access ─────────────────────────────────────────────────────

def test_shared_store_access_default_false():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Ella")
        assert acct.shared_store_access is False
    finally:
        _cleanup(path)


def test_set_shared_store_access():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Fay")
        mgr.set_shared_store_access(acct.account_id, True)
        loaded = mgr.get(acct.account_id)
        assert loaded.shared_store_access is True
    finally:
        _cleanup(path)


def test_set_shared_store_access_nonexistent():
    mgr, path = _fresh_mgr()
    try:
        ok = mgr.set_shared_store_access("acc_doesnotexist", True)
        assert ok is False
    finally:
        _cleanup(path)


# ── CRUD ────────────────────────────────────────────────────────────────────

def test_get_nonexistent_account():
    mgr, path = _fresh_mgr()
    try:
        assert mgr.get("acc_doesnotexist") is None
    finally:
        _cleanup(path)


def test_list_accounts():
    mgr, path = _fresh_mgr()
    try:
        mgr.register("User1")
        mgr.register("User2")
        mgr.register("User3")
        accounts = mgr.list_accounts()
        assert len(accounts) == 3
        names = {a.display_name for a in accounts}
        assert names == {"User1", "User2", "User3"}
    finally:
        _cleanup(path)


# ── Legacy Key Registration ─────────────────────────────────────────────────

def test_register_legacy_key():
    mgr, path = _fresh_mgr()
    try:
        acct, _ = mgr.register("Admin")
        legacy_token = "my-old-api-token-from-dot-file"
        ok = mgr.register_legacy_key(acct.account_id, legacy_token, "legacy")
        assert ok is True
        # Should authenticate with the legacy token
        authed = mgr.authenticate(legacy_token)
        assert authed is not None
        assert authed.account_id == acct.account_id
    finally:
        _cleanup(path)


def test_register_legacy_key_nonexistent_account():
    mgr, path = _fresh_mgr()
    try:
        ok = mgr.register_legacy_key("acc_nope", "some-key")
        assert ok is False
    finally:
        _cleanup(path)


# ── Account Data Model ──────────────────────────────────────────────────────

def test_account_to_dict_and_back():
    acct = Account(
        account_id="acc_test123456",
        display_name="Test User",
        email="test@test.com",
        role="admin",
        discord_user_id=12345,
        shared_store_access=True,
        created_at="2024-01-01T00:00:00Z",
        daily_cap_usd=25.0,
    )
    d = acct.to_dict()
    restored = Account.from_dict(d)
    assert restored.account_id == acct.account_id
    assert restored.display_name == acct.display_name
    assert restored.email == acct.email
    assert restored.role == acct.role
    assert restored.discord_user_id == acct.discord_user_id
    assert restored.shared_store_access == acct.shared_store_access
    assert restored.created_at == acct.created_at
    assert restored.daily_cap_usd == acct.daily_cap_usd


def test_api_key_format():
    """API keys should have the expected format."""
    key = _gen_api_key()
    assert key.startswith("sk_live_")
    assert len(key) > 40  # "sk_live_" + 32 urlsafe chars


def test_key_hash_deterministic():
    """Same key should always produce the same hash."""
    key = "sk_live_test_key_value"
    h1 = _hash_key(key)
    h2 = _hash_key(key)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_different_keys_different_hashes():
    h1 = _hash_key("key_a")
    h2 = _hash_key("key_b")
    assert h1 != h2


# ── Workspace Integration ──────────────────────────────────────────────────

def test_workspace_registry_account_id():
    """WorkspaceRegistry.add() should store account_id and list_keys() should filter by it."""
    from workspaces import WorkspaceRegistry
    import tempfile, json

    # Create a temp workspaces.json
    fd, ws_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(ws_path, "w") as f:
        json.dump({}, f)

    import config
    old_path = config.WORKSPACES_PATH
    config.WORKSPACES_PATH = ws_path
    try:
        reg = WorkspaceRegistry()
        reg.add("app1", "/tmp/app1", owner_id=100, account_id="acc_aaa")
        reg.add("app2", "/tmp/app2", owner_id=200, account_id="acc_bbb")
        reg.add("app3", "/tmp/app3", owner_id=100, account_id="acc_aaa")

        # Filter by account_id
        keys_a = reg.list_keys(account_id="acc_aaa")
        assert sorted(keys_a) == ["app1", "app3"]

        keys_b = reg.list_keys(account_id="acc_bbb")
        assert keys_b == ["app2"]

        # get_account_id
        assert reg.get_account_id("app1") == "acc_aaa"
        assert reg.get_account_id("app2") == "acc_bbb"
        assert reg.get_account_id("nonexistent") is None

        # can_access with account_id
        assert reg.can_access("app1", user_id=0, is_admin=False, account_id="acc_aaa") is True
        assert reg.can_access("app1", user_id=0, is_admin=False, account_id="acc_bbb") is False

        # is_owner with account_id
        assert reg.is_owner("app1", account_id="acc_aaa") is True
        assert reg.is_owner("app1", account_id="acc_bbb") is False
    finally:
        config.WORKSPACES_PATH = old_path
        os.unlink(ws_path)


# ── Config Changes ──────────────────────────────────────────────────────────

def test_config_has_new_vars():
    """config.py should define the new multi-tenant variables."""
    import config
    assert hasattr(config, "ACCOUNTS_PATH")
    assert hasattr(config, "CREDENTIAL_ENCRYPTION_KEY")
    assert hasattr(config, "RUN_MODE")
    assert config.RUN_MODE in ("full", "api_only", "bot_only")


def test_config_validate_api_only_mode():
    """In api_only mode, missing Discord vars should not cause validation errors."""
    import config
    old_mode = config.RUN_MODE
    old_token = config.DISCORD_BOT_TOKEN
    old_uid = config.DISCORD_ALLOWED_USER_ID
    try:
        config.RUN_MODE = "api_only"
        config.DISCORD_BOT_TOKEN = ""
        config.DISCORD_ALLOWED_USER_ID = 0
        problems = config.validate()
        discord_problems = [p for p in problems if "DISCORD" in p]
        assert discord_problems == [], f"api_only mode should not require Discord vars: {discord_problems}"
    finally:
        config.RUN_MODE = old_mode
        config.DISCORD_BOT_TOKEN = old_token
        config.DISCORD_ALLOWED_USER_ID = old_uid
