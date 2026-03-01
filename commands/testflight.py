"""
commands/testflight.py — /testflight: archive, export IPA, upload to TestFlight.

Prerequisites:
  - Xcode installed + signed into Apple Developer account
  - App Store Connect API key (.p8) at ~/.private_keys/AuthKey_<KEY_ID>.p8
  - Env vars: APPLE_TEAM_ID, ASC_KEY_ID, ASC_ISSUER_ID
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable, Optional

import config
from asc_api import ensure_app, poll_build_processing
from platforms import iOSPlatform, export_ipa, testflight_upload, preflight_fix_ios, validate_ipa


async def _with_heartbeat(coro, on_status, label: str, interval: int = 30):
    """Run a coroutine while sending periodic status updates."""
    task = asyncio.ensure_future(coro)
    elapsed = 0
    while not task.done():
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            elapsed += interval
            mins, secs = divmod(elapsed, 60)
            ts = f"{mins}m {secs}s" if mins else f"{secs}s"
            await on_status(f"  Still {label.lower()}... ({ts})", None)
    return task.result()


@dataclass
class TestFlightResult:
    success: bool
    needs_setup: bool = False
    app_name: str = ""
    bundle_id: str = ""


async def handle_testflight(
    workspace_key: str,
    workspace_path: str,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
) -> TestFlightResult:
    """Archive, export, and upload the current workspace's iOS app to TestFlight."""

    # Validate credentials
    team_id = config.APPLE_TEAM_ID
    key_id = config.ASC_KEY_ID
    issuer_id = config.ASC_ISSUER_ID

    missing = []
    if not team_id:
        missing.append("APPLE_TEAM_ID")
    if not key_id:
        missing.append("ASC_KEY_ID")
    if not issuer_id:
        missing.append("ASC_ISSUER_ID")
    if missing:
        await on_status(
            f"❌ Missing credentials: `{', '.join(missing)}`\n"
            "Set these in `.env` to enable TestFlight uploads.\n"
            "See `docs/testflight-plan.md` for setup instructions.",
            None,
        )
        return TestFlightResult(success=False)

    # Check .p8 key file
    key_path = config.ASC_KEY_PATH
    if key_path and not Path(key_path).exists():
        await on_status(f"❌ API key file not found: `{key_path}`", None)
        return TestFlightResult(success=False)

    start_time = time.time()

    # Step 1: Configure xcconfig
    await on_status(f"Configuring **{workspace_key}** for distribution...", None)
    iOSPlatform.set_team_id(workspace_path, team_id)

    build_number = int(time.time())
    iOSPlatform.set_build_number(workspace_path, build_number)

    bundle_id = iOSPlatform.parse_bundle_id(workspace_path)
    # Ensure the bundle ID is written to xcconfig so Xcode can use it
    if bundle_id:
        iOSPlatform.set_bundle_id(workspace_path, bundle_id)
    app_name = workspace_key.replace("-", " ").replace("_", " ").title()
    # Show config summary — note whose account is being used
    owner_note = ""
    if team_id == "825658FA35":
        owner_note = "  Signing with **Jared's** Apple Developer account.\n"
    await on_status(
        f"{owner_note}"
        f"  App ID: `{bundle_id or 'unknown'}` — unique identifier for this app\n"
        f"  Build #: `{build_number}` — version number for this upload\n"
        f"  Team: `{team_id}` — Apple Developer team used for signing",
        None,
    )

    # Step 2: Ensure app exists in App Store Connect
    if not bundle_id:
        await on_status("⚠️ Could not read bundle ID.", None)
        return TestFlightResult(success=False)

    await on_status("Checking App Store Connect...", None)
    ok, msg = await ensure_app(bundle_id, workspace_key)
    if not ok:
        return TestFlightResult(
            success=False, needs_setup=True,
            app_name=app_name, bundle_id=bundle_id,
        )

    await on_status(msg, None)

    # Step 3: Pre-flight fixes
    fixes = preflight_fix_ios(workspace_path)
    if fixes:
        await on_status("🔧 Auto-fixed: " + ", ".join(fixes), None)

    # Step 4: Archive
    await on_status("Archiving for distribution (Release build)...", None)
    archive_result = await _with_heartbeat(
        iOSPlatform.archive(workspace_path, team_id),
        on_status, label="Archiving",
    )
    if not archive_result.success:
        await on_status(
            f"❌ Archive failed:\n```\n{archive_result.error[:1200]}\n```",
            None,
        )
        return TestFlightResult(success=False)
    archive_path = archive_result.output
    await on_status("Archive succeeded.", None)

    # Step 4: Export IPA
    await on_status("Exporting IPA...", None)
    ok, ipa_or_error, raw = await _with_heartbeat(
        export_ipa(archive_path, workspace_path, team_id),
        on_status, label="Exporting",
    )
    if not ok:
        await on_status(f"❌ Export failed:\n```\n{ipa_or_error[:1200]}\n```", None)
        return TestFlightResult(success=False)
    ipa_path = ipa_or_error
    await on_status(f"IPA exported.", None)

    # Step 6: Validate before uploading
    await on_status("Validating with Apple...", None)
    valid, val_error = await _with_heartbeat(
        validate_ipa(ipa_path, key_id, issuer_id),
        on_status, label="Validating",
    )
    if not valid:
        await on_status(f"❌ Validation failed:\n```\n{val_error[:1500]}\n```", None)
        return TestFlightResult(success=False)
    await on_status("Validation passed.", None)

    # Step 7: Upload
    await on_status("Uploading to App Store Connect...", None)
    upload_result = await _with_heartbeat(
        testflight_upload(ipa_path, key_id, issuer_id),
        on_status, label="Uploading",
    )

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    if upload_result.success:
        await on_status(
            f"✅ **{workspace_key}** uploaded to TestFlight!\n"
            f"  Bundle: `{bundle_id}` · Build: `{build_number}` · Time: {mins}m {secs}s",
            None,
        )
        # Poll for processing completion in the background
        async def _notify_ready(msg):
            await on_status(msg, None)
        asyncio.create_task(poll_build_processing(
            bundle_id, str(build_number), on_ready=_notify_ready,
        ))
        return TestFlightResult(success=True)
    else:
        await on_status(upload_result.message, None)
        return TestFlightResult(success=False)
