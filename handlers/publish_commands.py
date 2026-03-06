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
from views.testflight_views import _testflight_setup_embed, _testflight_success_embed
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


HANDLERS = {
    "testflight": handle_testflight_cmd,
    "playstore": handle_playstore_cmd,
}
