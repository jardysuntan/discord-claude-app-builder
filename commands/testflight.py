"""
commands/testflight.py — /testflight: archive, export IPA, upload to TestFlight.

Prerequisites:
  - Xcode installed + signed into Apple Developer account
  - App Store Connect API key (.p8) at ~/.private_keys/AuthKey_<KEY_ID>.p8
  - Env vars: APPLE_TEAM_ID, ASC_KEY_ID, ASC_ISSUER_ID
  - App record created in App Store Connect with matching bundle ID
"""

import time
from pathlib import Path
from typing import Callable, Awaitable, Optional

import config
from platforms import iOSPlatform, export_ipa, testflight_upload


async def handle_testflight(
    workspace_key: str,
    workspace_path: str,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
) -> None:
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
        return

    # Check .p8 key file
    key_path = config.ASC_KEY_PATH
    if key_path and not Path(key_path).exists():
        await on_status(f"❌ API key file not found: `{key_path}`", None)
        return

    start_time = time.time()

    # Step 1: Configure xcconfig
    await on_status(f"Configuring **{workspace_key}** for distribution...", None)
    iOSPlatform.set_team_id(workspace_path, team_id)

    build_number = int(time.time())
    iOSPlatform.set_build_number(workspace_path, build_number)

    bundle_id = iOSPlatform.parse_bundle_id(workspace_path)
    await on_status(
        f"  Bundle: `{bundle_id or 'unknown'}`\n"
        f"  Build: `{build_number}`\n"
        f"  Team: `{team_id}`",
        None,
    )

    # Step 2: Archive
    await on_status("Archiving for distribution (Release build)...\nThis may take a few minutes.", None)
    archive_result = await iOSPlatform.archive(workspace_path, team_id)
    if not archive_result.success:
        await on_status(
            f"❌ Archive failed:\n```\n{archive_result.error[:1200]}\n```",
            None,
        )
        return
    archive_path = archive_result.output
    await on_status("Archive succeeded.", None)

    # Step 3: Export IPA
    await on_status("Exporting IPA...", None)
    ok, ipa_or_error, raw = await export_ipa(archive_path, workspace_path, team_id)
    if not ok:
        await on_status(f"❌ Export failed:\n```\n{ipa_or_error[:1200]}\n```", None)
        return
    ipa_path = ipa_or_error
    await on_status(f"IPA exported.", None)

    # Step 4: Upload
    await on_status("Uploading to App Store Connect...", None)
    upload_result = await testflight_upload(ipa_path, key_id, issuer_id)

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    if upload_result.success:
        await on_status(
            f"✅ **{workspace_key}** uploaded to TestFlight!\n\n"
            f"  Bundle: `{bundle_id}`\n"
            f"  Build: `{build_number}`\n"
            f"  Time: {mins}m {secs}s\n\n"
            "The build will appear in App Store Connect after processing (5-30 min).\n"
            "Then add testers in TestFlight.",
            None,
        )
    else:
        await on_status(upload_result.message, None)
