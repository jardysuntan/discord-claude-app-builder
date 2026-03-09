"""
handlers/publish_commands.py — Publish commands (testflight, playstore).

Extracted from bot.py lines 2107-2182.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import config
from commands.playstore import handle_playstore
from commands.playstore_state import PlayStoreState
from commands.testflight import handle_testflight
from views.testflight_views import _testflight_setup_embed, _testflight_success_embed, _notify_admin
from views.playstore_views import (
    PlayStoreChecklistView,
    _playstore_checklist_embed,
    _playstore_success_embed,
    _EmailAABView,
)

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_testflight_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            async def tf_status(msg, fpath=None):
                await ctx.send(channel, msg, file_path=fpath)

            result = await handle_testflight(ws_key, ws_path, on_status=tf_status)
            if result and result.needs_setup:
                await _notify_admin(ctx, user_id, result.app_name, result.bundle_id)
                embed, view = _testflight_setup_embed(
                    ctx, user_id, ws_key, ws_path,
                    result.app_name, result.bundle_id,
                )
                await channel.send(embed=embed, view=view)
            elif result and result.success:
                await channel.send(embed=_testflight_success_embed(
                    ws_key, result.bundle_id,
                ))


async def handle_playstore_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            from platforms import AndroidPlatform as _AP
            pkg = _AP.parse_app_id(ws_path) or ""
            app_name = ws_key.replace("-", " ").replace("_", " ").title()
            state = PlayStoreState.load(ws_path)

            # Use per-workspace key, or fall back to global key from .env
            effective_key = state.json_key_path if state.has_json_key() else config.PLAY_JSON_KEY_PATH
            if effective_key:
                # Key available -- build & upload directly
                async def ps_status(msg, fpath=None):
                    await ctx.send(channel, msg, file_path=fpath)

                result = await handle_playstore(
                    ws_key, ws_path, on_status=ps_status,
                    key_path=effective_key,
                )
                if result and result.success:
                    state.last_upload_version_code = result.version_code
                    state.last_upload_timestamp = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
                    )
                    state.save(ws_path)
                    await channel.send(embed=_playstore_success_embed(
                        ws_key, pkg or result.package_name,
                    ))
                elif result and result.first_upload and result.aab_path:
                    email_view = _EmailAABView(
                        user_id, result.aab_path,
                        ws_key, pkg, channel,
                    )
                    await channel.send(
                        "📧 **Enter your email** to receive the AAB file, "
                        "then upload it to Play Console.\n"
                        "*(This is only needed for the first upload — "
                        "future uploads are automatic)*",
                        view=email_view,
                    )
            else:
                # Show checklist
                state.save(ws_path)  # persist initial state
                view = PlayStoreChecklistView(
                    ctx, user_id, ws_key, ws_path, app_name, pkg,
                )
                await channel.send(
                    embed=_playstore_checklist_embed(ws_key, app_name, pkg, view.state),
                    view=view,
                )


def _rename_app_in_workspace(ws_path: str, new_name: str) -> list[str]:
    """Rename the app display name across all workspace files. Returns list of updated files."""
    import re
    from pathlib import Path

    updated = []
    root = Path(ws_path)

    # 1. Config.xcconfig — APP_NAME or PRODUCT_NAME
    xcconfig = root / "iosApp" / "Configuration" / "Config.xcconfig"
    if xcconfig.exists():
        text = xcconfig.read_text()
        new_text = re.sub(r'^(APP_NAME\s*=\s*).*$', rf'\g<1>{new_name}', text, flags=re.MULTILINE)
        new_text = re.sub(r'^(PRODUCT_NAME\s*=\s*).*$', rf'\g<1>{new_name}', new_text, flags=re.MULTILINE)
        if new_text != text:
            xcconfig.write_text(new_text)
            updated.append("Config.xcconfig")

    # 2. AndroidManifest.xml — android:label
    manifest = root / "composeApp" / "src" / "androidMain" / "AndroidManifest.xml"
    if manifest.exists():
        text = manifest.read_text()
        new_text = re.sub(r'android:label="[^"]*"', f'android:label="{new_name}"', text)
        if new_text != text:
            manifest.write_text(new_text)
            updated.append("AndroidManifest.xml")

    # 3. strings.xml — app_name
    strings = root / "composeApp" / "src" / "androidMain" / "res" / "values" / "strings.xml"
    if strings.exists():
        text = strings.read_text()
        new_text = re.sub(
            r'(<string name="app_name">)[^<]*(</string>)',
            rf'\g<1>{new_name}\g<2>', text,
        )
        if new_text != text:
            strings.write_text(new_text)
            updated.append("strings.xml")

    # 4. settings.gradle.kts — rootProject.name
    settings = root / "settings.gradle.kts"
    if settings.exists():
        text = settings.read_text()
        new_text = re.sub(r'rootProject\.name\s*=\s*"[^"]*"', f'rootProject.name = "{new_name}"', text)
        if new_text != text:
            settings.write_text(new_text)
            updated.append("settings.gradle.kts")

    # 5. index.html — <title>
    index = root / "composeApp" / "src" / "webMain" / "resources" / "index.html"
    if index.exists():
        text = index.read_text()
        new_text = re.sub(r'<title>[^<]*</title>', f'<title>{new_name}</title>', text)
        if new_text != text:
            index.write_text(new_text)
            updated.append("index.html")

    return updated


async def handle_appname_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    """Rename the app everywhere: workspace key, files, and App Store Connect."""
    new_name = (cmd.raw_cmd or "").strip()
    if not new_name:
        await ctx.send(channel, "Usage: `/appname My New Name`")
        return

    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
        return

    if not ctx.registry.can_access(ws_key, user_id, is_admin):
        await ctx.send(channel, "You don't have access to that workspace.")
        return

    # 1. Rename workspace key in registry
    new_key = new_name.lower().replace(" ", "-")
    if new_key != ws_key:
        if ctx.registry.get_path(new_key):
            await ctx.send(channel, f"❌ Workspace `{new_key}` already exists.")
            return
        ctx.registry.rename(ws_key, new_key)

    # 2. Rename in workspace files
    _rename_app_in_workspace(ws_path, new_name)

    # 3. Rename in App Store Connect (if app exists)
    from platforms import iOSPlatform
    bundle_id = iOSPlatform.parse_bundle_id(ws_path)
    asc_note = ""
    if bundle_id:
        try:
            from asc_api import update_app_name
            await update_app_name(bundle_id, new_name)
        except Exception as e:
            err = str(e)[:200]
            if "No app found" not in err:
                asc_note = f"\n⚠️ App Store Connect rename failed: {err}"

    await ctx.send(channel, f"✅ Renamed to **{new_name}**{asc_note}")


HANDLERS = {
    "testflight": handle_testflight_cmd,
    "playstore": handle_playstore_cmd,
    "appname": handle_appname_cmd,
    "rename": handle_appname_cmd,
}
