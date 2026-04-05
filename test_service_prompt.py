"""Tests for service.send_prompt and completion webhook behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import service
from agent_loop import AgentLoopResult


def test_emit_completion_event_posts_complete_payload():
    status = service.BuildStatus(
        build_id="build123",
        slug="demo-app",
        status="success",
        phase="complete",
        message="Done",
        platforms={"web": {"success": True}},
        elapsed_seconds=12,
        webhook_url="http://localhost:9999/hook",
    )

    with patch("service._send_webhook_event", new_callable=AsyncMock) as mock_send:
        asyncio.run(service._emit_completion_event(status))

    mock_send.assert_awaited_once()
    webhook_url, payload = mock_send.await_args.args
    assert webhook_url == "http://localhost:9999/hook"
    assert payload["event"] == "complete"
    assert payload["detail"]["slug"] == "demo-app"


def test_send_prompt_tracks_workspace_slug_and_uses_request_webhook():
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        return SimpleNamespace(cancel=lambda: None)

    registry = SimpleNamespace(get_path=lambda slug: "/tmp/demo-workspace")
    runner = SimpleNamespace()
    loop_result = AgentLoopResult(
        success=True,
        total_attempts=1,
        total_duration_secs=1.0,
        attempts=[],
        final_message="ok",
    )

    with patch("service._get_registry", return_value=registry), \
         patch("service._get_agent_runner", return_value=runner), \
         patch("service.run_agent_loop", new_callable=AsyncMock, return_value=loop_result), \
         patch("service.format_loop_summary", return_value="Loop passed"), \
         patch("service._track_build") as mock_track, \
         patch("service._send_webhook_event", new_callable=AsyncMock) as mock_webhook, \
         patch("service.asyncio.create_task", side_effect=fake_create_task):
        status = asyncio.run(
            service.send_prompt(
                service.PromptRequest(
                    workspace="demo-app",
                    prompt="make it better",
                    webhook_url="http://localhost:9999/prompt",
                )
            )
        )
        assert status.webhook_url == "http://localhost:9999/prompt"
        assert len(scheduled) == 1
        asyncio.run(scheduled[0])

    mock_track.assert_called_once_with("prompt", "demo-app", True, 0)
    mock_webhook.assert_awaited()
    webhook_url, payload = mock_webhook.await_args.args
    assert webhook_url == "http://localhost:9999/prompt"
    assert payload["detail"]["slug"] == "demo-app"
