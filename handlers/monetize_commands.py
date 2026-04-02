"""
handlers/monetize_commands.py — Handler for /monetize command.

Scaffolds RevenueCat subscription integration into a KMP workspace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands.monetize import run_monetize

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_monetize(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
        return

    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set. Use `/create <AppName>` first.")
        return

    if not ctx.registry.can_access(ws_key, user_id, is_admin):
        await ctx.send(channel, "You don't have access to that workspace.")
        return

    await ctx.send(
        channel,
        f"💰 **Adding RevenueCat subscriptions** to **{ws_key}**...\n"
        "This will add the SDK, generate a paywall UI, and wire up entitlement checks.",
    )

    async def on_status(msg, fpath=None):
        await ctx.send(channel, msg, file_path=fpath)

    platform = ctx.registry.get_platform(user_id) or "android"

    success, next_steps = await run_monetize(
        raw_args=cmd.raw_cmd,
        workspace_key=ws_key,
        workspace_path=ws_path,
        claude=ctx.claude,
        on_status=on_status,
        platform=platform,
    )

    if success:
        await ctx.send(
            channel,
            f"✅ **Subscriptions added to {ws_key}!** Run `/demo` to see the paywall.\n\n"
            f"{next_steps}",
        )
    else:
        await ctx.send(
            channel,
            f"⚠️ Monetization integration had build issues — check errors above.\n\n"
            f"{next_steps}",
        )


HANDLERS = {
    "monetize": handle_monetize,
}
