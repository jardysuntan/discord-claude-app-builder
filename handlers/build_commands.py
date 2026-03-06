"""
handlers/build_commands.py — Build, demo, deploy, and related commands
(buildapp, build, platform, demo, deploy, vid, fix, widget).

Extracted from bot.py lines 2002-2234.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from bot_context import STILL_LISTENING
from commands import fix, widget
from helpers.demo_runner import run_demo
from platforms import (
    build_platform,
    deploy_ios,
    deploy_android,
    AndroidPlatform,
)
from views.buildapp_views import _BuildAppView
from views.demo_views import DemoPlatformView
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


async def handle_build(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            platform = cmd.platform or "android"
            await ctx.send(channel, f"🔨 Building **{ws_key}** [{platform}]...")
            await ctx.send(channel, STILL_LISTENING)
            result = await build_platform(platform, ws_path)
            if result.success:
                await ctx.send(channel, f"✅ {platform.upper()} build succeeded.")
            else:
                await ctx.send(
                    channel,
                    f"❌ {platform.upper()} build failed:\n```\n{result.error[:1200]}\n```",
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


async def handle_deploy(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not is_admin:
        await ctx.send(channel, "🔒 `/deploy` is admin-only. Use `/testflight` instead.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            platform = cmd.platform or "ios"
            await ctx.send(
                channel,
                f"📲 Deploying **{ws_key}** to {platform.upper()} device...",
            )
            if platform == "ios":
                result = await deploy_ios(ws_path)
                await ctx.send(channel, result.message)
            elif platform == "android":
                result = await deploy_android(ws_path)
                await ctx.send(channel, result.message)
            else:
                await ctx.send(
                    channel,
                    f"❌ Deploy supports `ios` or `android`, not `{platform}`.",
                )


async def handle_vid(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not is_admin:
        await ctx.send(channel, "🔒 `/vid` is admin-only.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            await ctx.send(channel, f"🎥 Recording **{ws_key}**...")
            ok, msg = await AndroidPlatform.ensure_device()
            if not ok:
                await ctx.send(channel, f"❌ {msg}")
            else:
                result = await AndroidPlatform.build(ws_path)
                if not result.success:
                    await ctx.send(
                        channel,
                        f"❌ Build failed:\n```\n{result.error[:800]}\n```",
                    )
                else:
                    await AndroidPlatform.launch(ws_path)
                    video = await AndroidPlatform.record()
                    await ctx.send(channel, "✅ Recording captured.", file_path=video)


async def handle_fix(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            await ctx.send(channel, STILL_LISTENING)

            async def fix_status(msg, fpath=None):
                await ctx.send(channel, msg, file_path=fpath)

            await fix.handle_fix(
                cmd.raw_cmd or "", ws_key, ws_path, ctx.claude,
                on_status=fix_status,
            )


async def handle_widget(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not is_admin:
        await ctx.send(channel, "🔒 `/widget` is admin-only (iOS feature).")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        else:
            async def widget_status(msg, fpath=None):
                await ctx.send(channel, msg, file_path=fpath)

            await widget.handle_widget(
                cmd.raw_cmd or "", ws_key, ws_path, ctx.claude,
                on_status=widget_status,
            )


HANDLERS = {
    "buildapp": handle_buildapp,
    "build": handle_build,
    "platform": handle_platform,
    "demo": handle_demo,
    "deploy": handle_deploy,
    "vid": handle_vid,
    "fix": handle_fix,
    "widget": handle_widget,
}
