"""
handlers/variant_commands.py — /try-variants command handler.

Spawns parallel Claude Code sessions in isolated branches, posts
screenshot previews, and lets users react to pick a winner.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

import config
from bot_context import STILL_LISTENING
from commands.try_variants import (
    VARIANT_EMOJIS,
    run_try_variants,
    merge_winner,
    build_variants_embed,
)
from helpers.progress import ProgressMessage

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


# How long to wait for a reaction vote (seconds)
_VOTE_TIMEOUT = 300


async def handle_try_variants(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
        return

    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
        return
    if not ctx.registry.can_access(ws_key, user_id, is_admin):
        await ctx.send(channel, "You don't have access to that workspace.")
        return

    # Parse: /try-variants [count] <prompt>
    raw = cmd.raw_cmd or ""
    parts = raw.split(None, 1)
    variant_count = 2
    prompt = raw

    if parts and parts[0].isdigit():
        variant_count = max(2, min(int(parts[0]), 3))
        prompt = parts[1] if len(parts) > 1 else ""

    if not prompt.strip():
        await ctx.send(
            channel,
            "Usage: `/try-variants [2|3] <prompt>`\n"
            "Example: `/try-variants 3 Build a todo app with dark mode`",
        )
        return

    await ctx.send(
        channel,
        f"🏁 Starting **{variant_count} parallel variants** for **{ws_key}**...\n"
        f"Each variant gets its own branch and Claude session.",
    )
    await ctx.send(channel, STILL_LISTENING)

    progress = ProgressMessage(ctx, channel, title=f"Try Variants — {ws_key}")

    try:
        result = await run_try_variants(
            prompt=prompt,
            ws_key=ws_key,
            ws_path=ws_path,
            claude=ctx.claude,
            variant_count=variant_count,
            on_status=progress.status_callback,
        )
    except Exception as exc:
        await progress.close()
        await ctx.send(channel, f"❌ Variant run failed: {exc}")
        return

    await progress.close()

    # Post results embed
    embed = build_variants_embed(ws_key, result.variants)
    embed_msg = await channel.send(embed=embed)

    # Attach screenshot previews
    successful = [v for v in result.variants if v.success]
    for v in result.variants:
        if v.screenshot_path:
            try:
                await channel.send(
                    f"📸 **Variant {v.index + 1}** — {v.label}",
                    file=discord.File(v.screenshot_path),
                )
            except Exception:
                pass
        elif not v.success:
            await ctx.send(channel, f"❌ Variant {v.index + 1} ({v.label}) failed: {v.error_message[:300]}")

    if not successful:
        await ctx.send(channel, "❌ All variants failed. No winner to pick.")
        return

    # Add reaction emojis for voting
    for v in successful:
        if v.index < len(VARIANT_EMOJIS):
            try:
                await embed_msg.add_reaction(VARIANT_EMOJIS[v.index])
            except Exception:
                pass

    await ctx.send(
        channel,
        f"⬆️ React on the embed above to pick the winner! "
        f"(waiting {_VOTE_TIMEOUT // 60} minutes)",
    )

    # Wait for a reaction from the original user
    def check(reaction: discord.Reaction, user: discord.User) -> bool:
        return (
            user.id == user_id
            and reaction.message.id == embed_msg.id
            and str(reaction.emoji) in VARIANT_EMOJIS
        )

    try:
        reaction, _ = await ctx.client.wait_for(
            "reaction_add", timeout=_VOTE_TIMEOUT, check=check,
        )
    except asyncio.TimeoutError:
        await ctx.send(channel, "⏰ No vote received — variants left on their branches for manual merge.")
        return

    # Determine winner
    winner_idx = VARIANT_EMOJIS.index(str(reaction.emoji))
    winner = next((v for v in result.variants if v.index == winner_idx), None)
    if not winner or not winner.success:
        await ctx.send(channel, f"❌ Variant {winner_idx + 1} didn't succeed — pick another.")
        return

    merge_msg = await merge_winner(ws_path, winner, result.variants)
    await ctx.send(channel, merge_msg)

    # Clear variant sessions so the main session continues cleanly
    for v in result.variants:
        variant_ws_key = f"{ws_key}__variant{v.index}"
        ctx.claude.clear_session(variant_ws_key)


HANDLERS = {
    "try-variants": handle_try_variants,
}
