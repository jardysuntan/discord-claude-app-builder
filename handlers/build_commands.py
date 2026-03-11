"""
handlers/build_commands.py — Build, demo, and related commands
(buildapp, platform, demo).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from helpers.demo_runner import run_demo
from views.buildapp_views import _BuildAppView
from views.deploy_embeds import _ios_deploy_info_embed

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_buildapp(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        prefill = cmd.raw_cmd or ""
        view = _BuildAppView(ctx, channel, user_id, is_admin, prefill)
        await channel.send(
            "**Let's build an app!** Tap the button to get started.",
            view=view,
        )


async def handle_platform(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if cmd.platform and cmd.platform in ("ios", "android") and not is_admin:
        if cmd.platform == "ios":
            await ctx.send(
                channel,
                "🔒 `/platform ios` is admin-only. Use `/platform web`, "
                "or `/testflight` to publish to the iOS App Store for testing.",
            )
        else:
            await ctx.send(
                channel,
                "🔒 `/platform android` is admin-only. Use `/platform web`, "
                "or `/playstore` to publish to Google Play Store for testing.",
            )
    elif cmd.platform and cmd.platform in ("ios", "android", "web"):
        ctx.registry.set_platform(user_id, cmd.platform)
        await ctx.send(channel, f"✅ Default demo platform set to **{cmd.platform}**.")
        if cmd.platform == "ios":
            await channel.send(embed=_ios_deploy_info_embed())
    elif cmd.platform:
        await ctx.send(
            channel, "❌ Unknown platform. Use `/platform ios`, `android`, or `web`."
        )
    else:
        current = ctx.registry.get_platform(user_id)
        await ctx.send(
            channel,
            f"📱 Your demo platform: **{current or 'web (default)'}**\n"
            "Change with `/platform ios`, `android`, or `web`.",
        )


async def handle_demo(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        elif not ctx.registry.can_access(ws_key, user_id, is_admin):
            await ctx.send(channel, "You don't have access to that workspace.")
        elif cmd.platform and cmd.platform in ("ios", "android") and not is_admin:
            if cmd.platform == "ios":
                await ctx.send(
                    channel,
                    "🔒 `/demo ios` is admin-only. Use `/demo web` to test in your browser, "
                    "or `/testflight` to publish to the iOS App Store for testing.",
                )
            else:
                await ctx.send(
                    channel,
                    "🔒 `/demo android` is admin-only. Use `/demo web` to test in your browser, "
                    "or `/playstore` to publish to Google Play Store for testing.",
                )
        elif cmd.platform:
            # /demo android, /demo ios, /demo web -> run directly
            prev = ctx.registry.get_platform(user_id)
            if prev != cmd.platform:
                ctx.registry.set_platform(user_id, cmd.platform)
                await ctx.send(
                    channel,
                    f"📌 **{cmd.platform.upper()}** is now your preferred demo platform.",
                )
                if cmd.platform == "ios":
                    await channel.send(embed=_ios_deploy_info_embed())
            await run_demo(ctx, channel, ws_key, ws_path, cmd.platform)
        else:
            # /demo -> auto-pick from preference, default to web
            platform = ctx.registry.get_platform(user_id) or "web"
            # Non-admin can only demo web
            if platform in ("ios", "android") and not is_admin:
                platform = "web"
            await run_demo(ctx, channel, ws_key, ws_path, platform)


HANDLERS = {
    "buildapp": handle_buildapp,
    "platform": handle_platform,
    "demo": handle_demo,
}
