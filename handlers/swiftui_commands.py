"""
handlers/swiftui_commands.py — Handler for /swiftui command.

Converts a KMP workspace's iOS layer from Compose Multiplatform to native SwiftUI.
Admin-only (significant compute cost).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands.swiftui import handle_swiftui
from helpers.demo_runner import run_demo

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_swiftui_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
        return

    if not is_admin:
        await ctx.send(channel, "🔒 Admin only — this command uses significant compute.")
        return

    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set. Use `/use <workspace>` first.")
        return

    if not ctx.registry.can_access(ws_key, user_id, is_admin):
        await ctx.send(channel, "You don't have access to that workspace.")
        return

    await ctx.send(
        channel,
        f"🔄 **Converting {ws_key} iOS layer to SwiftUI**\n"
        "This will add SKIE, build the Kotlin framework, and rewrite "
        "ContentView.swift with native SwiftUI screens.\n"
        "_(This takes several minutes — I'll post progress updates.)_",
    )

    async def on_status(msg):
        await ctx.send(channel, msg)

    result = await handle_swiftui(
        workspace_key=ws_key,
        workspace_path=ws_path,
        claude=ctx.claude,
        on_status=on_status,
    )

    if result.success:
        await ctx.send(
            channel,
            f"✅ **{ws_key}** iOS layer converted to SwiftUI!\n\n"
            f"{result.message}\n\n"
            "Launching iOS demo...",
        )
        await run_demo(ctx, channel, ws_key, ws_path, "ios")
        await ctx.send(
            channel,
            "**Next steps:**\n"
            "• `/testflight` — publish to TestFlight\n"
            "• Send a prompt like `@workspace adjust the SwiftUI colors` to refine",
        )
    else:
        await ctx.send(
            channel,
            f"❌ **SwiftUI conversion failed** for {ws_key}.\n\n{result.message}",
        )


HANDLERS = {
    "swiftui": handle_swiftui_cmd,
}
