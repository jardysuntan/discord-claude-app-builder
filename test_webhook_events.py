"""Tests for real-time webhook progress events in service.py."""

import asyncio
from unittest.mock import AsyncMock, patch

from service import _send_webhook_event, _infer_phase, _classify_event


# ── _send_webhook_event ─────────────────────────────────────────────────────

def test_send_webhook_event_skips_none():
    """No-op when webhook_url is None."""
    asyncio.run(_send_webhook_event(None, {"event": "test"}))


@patch("httpx.AsyncClient")
def test_send_webhook_event_posts(mock_client_cls):
    """Posts event JSON to the webhook URL."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    event = {"build_id": "abc", "event": "started"}
    asyncio.run(_send_webhook_event("http://localhost:9999", event))

    mock_client.post.assert_called_once_with("http://localhost:9999", json=event)


@patch("httpx.AsyncClient")
def test_send_webhook_event_swallows_errors(mock_client_cls):
    """Exceptions are logged, not raised."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("connection refused")
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    # Should not raise
    asyncio.run(_send_webhook_event("http://localhost:9999", {"event": "test"}))


# ── _infer_phase ────────────────────────────────────────────────────────────

def test_infer_phase_scaffolding():
    assert _infer_phase("🏗️ Creating project structure", "building") == "scaffolding"
    assert _infer_phase("scaffold the app", "building") == "scaffolding"


def test_infer_phase_credentials():
    assert _infer_phase("🔑 Patching credentials", "scaffolding") == "patching_credentials"
    assert _infer_phase("Setting up credential files", "scaffolding") == "patching_credentials"


def test_infer_phase_schema():
    assert _infer_phase("Schema design phase", "scaffolding") == "schema_design"
    assert _infer_phase("🗄️ Deploying database tables", "building") == "schema_deploy"
    assert _infer_phase("Supabase migration running", "building") == "schema_deploy"


def test_infer_phase_ios():
    assert _infer_phase("Building iOS target", "scaffolding") == "building_ios"
    assert _infer_phase("⚠️ iOS fix attempt 2", "building_ios") == "fixing"
    assert _infer_phase("iOS demo launching", "building_ios") == "demo_ios"


def test_infer_phase_android():
    assert _infer_phase("Building Android APK", "scaffolding") == "building_android"
    assert _infer_phase("⚠️ Android fix attempt", "building_android") == "fixing"
    assert _infer_phase("Android demo ready", "building_android") == "demo_android"


def test_infer_phase_web():
    assert _infer_phase("Building web bundle", "scaffolding") == "building_web"
    assert _infer_phase("⚠️ Web fix needed", "building_web") == "fixing"
    assert _infer_phase("Web demo deployed", "building_web") == "demo_web"


def test_infer_phase_deploy_and_save():
    assert _infer_phase("Deploying to Cloudflare", "building") == "deploying"
    assert _infer_phase("Saving workspace and committing", "deploying") == "saving"


def test_infer_phase_keeps_current():
    assert _infer_phase("random unrecognized message", "schema_deploy") == "schema_deploy"


def test_infer_phase_demo_emoji_fallback():
    assert _infer_phase("📱 Launching preview", "building") == "demo_android"


# ── _classify_event ─────────────────────────────────────────────────────────

def test_classify_event_platform_complete():
    etype, detail = _classify_event("✅ Android build passed!", "building_android")
    assert etype == "platform_complete"
    assert detail["platform"] == "android"
    assert detail["success"] is True


def test_classify_event_platform_complete_web():
    etype, detail = _classify_event("✅ Web build succeeded", "building_web")
    assert etype == "platform_complete"
    assert detail["platform"] == "web"


def test_classify_event_demo_ready():
    etype, detail = _classify_event("Web preview → http://example.com/demo", "demo_web")
    assert etype == "demo_ready"
    assert detail["platform"] == "web"
    assert detail["url"] == "http://example.com/demo"


def test_classify_event_demo_ready_live():
    etype, detail = _classify_event("Android live → http://example.com/app", "demo_android")
    assert etype == "demo_ready"
    assert detail["platform"] == "android"
    assert detail["url"] == "http://example.com/app"


def test_classify_event_issue():
    etype, detail = _classify_event("⚠️ Compilation failed, retrying", "fixing")
    assert etype == "issue"
    assert "Compilation failed" in detail["error"]


def test_classify_event_issue_retry():
    etype, detail = _classify_event("Build error: retry attempt 3", "fixing")
    assert etype == "issue"


def test_classify_event_progress():
    etype, detail = _classify_event("Setting up project files", "scaffolding")
    assert etype == "progress"
    assert detail == {}


def test_classify_event_not_fatal():
    """Messages with 'fatal' should not be classified as issue."""
    etype, _ = _classify_event("fatal crash in build", "building")
    assert etype == "progress"
