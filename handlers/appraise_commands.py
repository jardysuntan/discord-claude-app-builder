"""
handlers/appraise_commands.py — Standalone /appraise command handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.appraise import run_appraisal
from views.appraise_views import appraisal_embed

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_appraise_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "\u274c No workspace set.")
        return

    await ctx.send(channel, "\U0001f50d Appraising your app against store guidelines...")

    appraisal = await run_appraisal(ctx.claude, ws_key, ws_path, platform="both")
    if not appraisal:
        await ctx.send(channel, "\u274c Appraisal failed. Try again.")
        return

    embed = appraisal_embed(appraisal, platform="both")
    await channel.send(embed=embed)


HANDLERS = {
    "appraise": handle_appraise_cmd,
}
