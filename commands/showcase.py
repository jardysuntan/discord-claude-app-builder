"""
commands/showcase.py — Let anyone in a Discord server try apps.

/showcase <ws>     → build + video demo for the channel
/tryapp <ws>       → build + web link (or mirror link) for the requester
/showcase gallery  → list available apps
/done              → end mirror session
"""

import asyncio
import time
import os
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass

import config
from platforms import AndroidPlatform, WebPlatform


@dataclass
class PublishedApp:
    workspace_key: str
    app_name: str
    web_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    last_built: float = 0


_gallery: dict[str, PublishedApp] = {}
_current_mirror_user: Optional[int] = None
_mirror_lock = asyncio.Lock()


async def handle_showcase(ws_key, ws_path, on_status):
    """Build + demo video for the channel."""
    await on_status(f"🎬 Preparing **{ws_key}**...", None)

    # Try web build first (anyone can access)
    await on_status("🌐 Building web version...", None)
    web_result = await WebPlatform.full_demo(ws_path)
    if web_result.success:
        _gallery[ws_key] = PublishedApp(
            workspace_key=ws_key, app_name=ws_key,
            web_url=web_result.demo_url, last_built=time.time())
        await on_status(
            f"✅ **{ws_key}** web demo is live!\n\n"
            f"👉 **Try it now:** {web_result.demo_url}\n\n"
            f"Works on any phone or computer — just tap the link.",
            None,
        )
    else:
        await on_status("⚠️ Web build failed, falling back to Android recording...", None)

    # Android demo (screenshot + video for the channel)
    android_result = await AndroidPlatform.full_demo(ws_path)
    if android_result.success:
        if android_result.screenshot_path:
            await on_status(f"📸 **{ws_key}** on Android:", android_result.screenshot_path)
        video = await AndroidPlatform.record()
        if video:
            await on_status(f"🎬 **{ws_key}** demo:", video)

    if not web_result.success and not android_result.success:
        await on_status("❌ Could not build for any platform.", None)
        return

    await on_status(
        f"Want to interact with it? Use `/tryapp {ws_key}`",
        None,
    )


async def handle_tryapp(ws_key, ws_path, user_id, user_name, on_status):
    """Give a user access to try the app — prefer web, fall back to mirror."""
    global _current_mirror_user

    # Check if we have a web version ready
    if ws_key in _gallery and _gallery[ws_key].web_url:
        await on_status(
            f"📱 **{user_name}**, the app is ready!\n\n"
            f"👉 **Tap to try:** {_gallery[ws_key].web_url}\n\n"
            f"Works right in your browser — no install needed.",
            None,
        )
        return

    # Fall back to web build
    await on_status(f"🌐 Building web version for you, {user_name}...", None)
    web_result = await WebPlatform.full_demo(ws_path)
    if web_result.success:
        _gallery[ws_key] = PublishedApp(
            workspace_key=ws_key, app_name=ws_key,
            web_url=web_result.demo_url, last_built=time.time())
        await on_status(
            f"📱 **{user_name}**, ready!\n\n"
            f"👉 {web_result.demo_url}\n\n"
            f"Tap, swipe, use it like a real app.",
            None,
        )
        return

    # Last resort: Android mirror (single user)
    async with _mirror_lock:
        if _current_mirror_user and _current_mirror_user != user_id:
            await on_status("⏳ Someone else is using the emulator. Try again shortly.", None)
            return
        _current_mirror_user = user_id

    await on_status(f"📱 Building Android version for {user_name}...", None)
    from commands.scrcpy import start as start_mirror
    android_result = await AndroidPlatform.full_demo(ws_path)
    if android_result.success:
        mirror_msg = await start_mirror()
        await on_status(mirror_msg, None)
        await on_status(
            f"Session is yours for 5 minutes. Type `/done` when finished.",
            None,
        )
        asyncio.create_task(_auto_timeout(user_id, 300, on_status))
    else:
        _current_mirror_user = None
        await on_status("❌ Android build failed.", None)


async def handle_done(user_id):
    global _current_mirror_user
    if _current_mirror_user != user_id:
        return "You don't have an active session."
    from commands.scrcpy import stop
    await stop()
    _current_mirror_user = None
    return "✅ Session ended. Thanks for trying!"


async def handle_gallery(on_status):
    if not _gallery:
        await on_status("📱 No apps published yet. Use `/showcase <workspace>`.", None)
        return
    lines = ["📱 **App Gallery**\n"]
    for key, app in _gallery.items():
        url_hint = f" → {app.web_url}" if app.web_url else ""
        lines.append(f"  **{key}**{url_hint}")
        lines.append(f"    `/showcase {key}` · `/tryapp {key}`\n")
    await on_status("\n".join(lines), None)


async def _auto_timeout(user_id, seconds, on_status):
    global _current_mirror_user
    await asyncio.sleep(seconds)
    if _current_mirror_user == user_id:
        from commands.scrcpy import stop
        await stop()
        _current_mirror_user = None
        await on_status("⏱️ Session timed out. Use `/tryapp` for more time.", None)
