"""
handlers/fix_ios_commands.py — Handler for /fix-ios command.

Makes targeted edits to an existing SwiftUI iOS layer (does NOT regenerate from scratch).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands.fix_ios import handle_fix_ios
from helpers.demo_runner import run_demo

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_fix_ios_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
        return

    if not is_admin:
        await ctx.send(channel, "🔒 Admin only — this command uses significant compute.")
        return

    user_request = (cmd.raw_cmd or "").strip()
    if not user_request:
        await ctx.send(
            channel,
            "❌ Please describe what to fix or add.\n"
            "Example: `/fix-ios add theming with light, dark, cal, and neon themes`",
        )
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
        f"🔧 **Fixing iOS SwiftUI** for {ws_key}\n"
        f"Request: _{user_request}_\n"
        "_(Making targeted edits — not regenerating from scratch.)_",
    )

    async def on_status(msg):
        await ctx.send(channel, msg)

    result = await handle_fix_ios(
        workspace_key=ws_key,
        workspace_path=ws_path,
        claude=ctx.claude,
        on_status=on_status,
        user_request=user_request,
    )

    if result.success:
        await ctx.send(
            channel,
            f"✅ **iOS fix applied** for {ws_key}!\n\n{result.message}\n\n"
            "Launching iOS demo...",
        )
        await run_demo(ctx, channel, ws_key, ws_path, "ios")
    else:
        await ctx.send(
            channel,
            f"❌ **iOS fix failed** for {ws_key}.\n\n{result.message}",
        )


HANDLERS = {
    "fix-ios": handle_fix_ios_cmd,
}
