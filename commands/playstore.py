"""
commands/playstore.py — /playstore: build AAB, upload to Google Play internal testing.

Prerequisites:
  - Service account JSON key with Google Play Developer API access
  - App created in Google Play Console with internal testing enabled
  - Env var: PLAY_JSON_KEY_PATH
"""

import asyncio
import time
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable, Optional

import config
from play_api import check_app_exists, upload_aab, poll_processing
from platforms import AndroidPlatform, preflight_fix_android


async def _email_file(file_path: str, to_email: str, subject: str, body: str) -> bool:
    """Email a file as attachment via Gmail SMTP. Returns True on success."""
    loop = asyncio.get_event_loop()

    def _send():
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email.mime.text import MIMEText
        from email import encoders

        smtp_user = config.GMAIL_ADDRESS
        smtp_pass = config.GMAIL_APP_PASSWORD
        if not smtp_user or not smtp_pass:
            print("[playstore] Email not configured: GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing")
            return False

        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        fname = Path(file_path).name
        with open(file_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={fname}")
            msg.attach(part)

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_email, msg.as_string())
            print(f"[playstore] Email sent to {to_email}")
            return True
        except Exception as e:
            print(f"[playstore] Email send error: {e}")
            return False

    return await loop.run_in_executor(None, _send)


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
class PlayStoreResult:
    success: bool
    needs_setup: bool = False
    app_name: str = ""
    package_name: str = ""
    version_code: Optional[int] = None
    aab_path: Optional[str] = None          # set on upload failure so caller can offer manual upload
    first_upload: bool = False               # True when "Package not found" — needs manual first upload


async def handle_playstore(
    workspace_key: str,
    workspace_path: str,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    key_path: Optional[str] = None,
) -> PlayStoreResult:
    """Build AAB and upload to Google Play internal testing."""

    # Step 1: Validate credentials — prefer explicit key_path, fall back to config
    resolved_key = key_path or config.PLAY_JSON_KEY_PATH
    if not resolved_key:
        await on_status(
            "❌ No service account JSON key configured.\n"
            "Upload one via the Play Store checklist, or set `PLAY_JSON_KEY_PATH` in `.env`.",
            None,
        )
        return PlayStoreResult(success=False)

    resolved_key_path = Path(resolved_key).expanduser()
    if not resolved_key_path.exists():
        await on_status(f"❌ Service account key file not found: `{resolved_key_path}`", None)
        return PlayStoreResult(success=False)

    start_time = time.time()

    # Step 2: Parse applicationId
    package_name = AndroidPlatform.parse_app_id(workspace_path)
    if not package_name:
        await on_status("❌ Could not find `applicationId` in build.gradle.kts.", None)
        return PlayStoreResult(success=False)

    app_name = workspace_key.replace("-", " ").replace("_", " ").title()

    # Step 3: Set versionCode to current timestamp
    version_code = int(time.time())
    AndroidPlatform.set_version_code(workspace_path, version_code)

    await on_status(
        f"Configuring **{workspace_key}** for Play Store...\n"
        f"  Package: `{package_name}`\n"
        f"  Version code: `{version_code}`",
        None,
    )

    # Step 4: Check app exists in Play Console (non-blocking for first upload)
    await on_status("Checking Google Play Console...", None)
    ok, msg = await check_app_exists(package_name, key_path=resolved_key)
    if ok:
        await on_status(msg, None)
    else:
        await on_status(f"⚠️ Package not yet registered on Google Play — this is normal for first uploads.", None)

    # Step 5: Ensure signing keystore
    ks_path = config.ANDROID_KEYSTORE_PATH
    alias = config.ANDROID_KEY_ALIAS
    store_pw = config.ANDROID_KEYSTORE_PASSWORD
    key_pw = config.ANDROID_KEY_PASSWORD

    if ks_path:
        ks_path = str(Path(ks_path).expanduser())
        if not Path(ks_path).exists():
            await on_status(f"❌ Keystore not found: `{ks_path}`", None)
            return PlayStoreResult(success=False)
        await on_status(f"Using keystore: `{Path(ks_path).name}`", None)
    else:
        # Auto-generate per-workspace keystore
        if not store_pw:
            store_pw = secrets.token_urlsafe(16)
            key_pw = store_pw
        await on_status("Generating release keystore...", None)
        gen_ok, gen_result = await AndroidPlatform.generate_keystore(
            workspace_path, alias, store_pw,
        )
        if not gen_ok:
            await on_status(f"❌ Keystore generation failed:\n```\n{gen_result}\n```", None)
            return PlayStoreResult(success=False)
        ks_path = gen_result
        key_pw = key_pw or store_pw
        await on_status(f"Keystore generated: `{Path(ks_path).name}`", None)

    # Step 6: Inject signing config
    AndroidPlatform.inject_signing_config(workspace_path, ks_path, alias, store_pw, key_pw)

    # Step 7: Pre-flight fixes
    fixes = preflight_fix_android(workspace_path)
    if fixes:
        await on_status("🔧 Auto-fixed: " + ", ".join(fixes), None)

    # Step 8: Build release AAB
    await on_status("Building release bundle (AAB)...", None)
    build_result = await _with_heartbeat(
        AndroidPlatform.bundle_release(workspace_path),
        on_status, label="Building",
    )
    if not build_result.success:
        await on_status(
            f"❌ Build failed:\n```\n{build_result.error[:1200]}\n```",
            None,
        )
        return PlayStoreResult(success=False)
    aab_path = build_result.output
    await on_status("AAB built successfully.", None)

    # Step 9: Upload to Play Store
    await on_status("Uploading to Google Play...", None)
    upload_ok, upload_msg, uploaded_vc = await _with_heartbeat(
        upload_aab(package_name, aab_path, key_path=resolved_key),
        on_status, label="Uploading",
    )

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    if upload_ok:
        await on_status(
            f"✅ **{workspace_key}** uploaded to Google Play!\n"
            f"  Package: `{package_name}` · Version: `{version_code}` · Time: {mins}m {secs}s",
            None,
        )
        # Step 10: Poll for processing in background
        async def _notify_ready(msg):
            await on_status(msg, None)
        asyncio.create_task(poll_processing(
            package_name, version_code, on_ready=_notify_ready, key_path=resolved_key,
        ))
        return PlayStoreResult(success=True, version_code=uploaded_vc or version_code)
    else:
        is_first = "not found" in upload_msg.lower() or "Package not found" in upload_msg
        if is_first:
            await on_status(
                "⚠️ Google Play requires the **first build** to be uploaded manually.\n"
                "After that, future `/playstore` uploads will be fully automatic!",
                None,
            )
        else:
            await on_status(f"❌ {upload_msg}", None)
        return PlayStoreResult(success=False, aab_path=aab_path, first_upload=is_first)
