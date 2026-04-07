"""
handlers/design_commands.py — /build-from-design command handler.

Accepts a reference image and runs the visual diff loop until the app
matches the design or max iterations are reached.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import discord

import config
from commands.design_loop import handle_build_from_design

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


async def handle_build_from_design_cmd(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        return await ctx.send(channel, "Agent mode OFF.")

    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_key or not ws_path:
        return await ctx.send(
            channel,
            "No workspace set. Use `/buildapp` first or `/use <workspace>`.",
        )

    if not ctx.registry.can_access(ws_key, user_id, is_admin):
        return await ctx.send(channel, "You don't have access to that workspace.")

    # Check for pending image attachment — the bot.py on_message handler
    # doesn't pass attachments to command handlers directly, so we store
    # them on the command object.  If none, prompt the user.
    image_paths: list[str] = getattr(cmd, "_image_paths", [])
    if not image_paths:
        return await ctx.send(
            channel,
            "**Usage:** `/build-from-design` — attach a reference screenshot or "
            "Figma frame as an image.\n\n"
            "Drag-and-drop or paste an image along with the command.",
        )

    reference_path = image_paths[0]
    description = cmd.raw_cmd or ""

    async def on_status(msg: str, file_path: str | None):
        await ctx.send(channel, msg, file_path=file_path)

    result = await handle_build_from_design(
        reference_image_path=reference_path,
        description=description,
        registry=ctx.registry,
        claude=ctx.claude,
        on_status=on_status,
        user_id=user_id,
    )

    # Post side-by-side comparison
    ref = result.get("reference_path")
    final = result.get("final_screenshot_path")
    score = result.get("final_score", 0)
    iterations = result.get("iterations", 0)
    elapsed = result.get("elapsed", "?")

    status_icon = "checkmark" if result.get("success") else "warning"
    status_emoji = "\u2705" if result.get("success") else "\u26a0\ufe0f"

    summary = (
        f"{status_emoji} **Design Loop Complete**\n\n"
        f"  Similarity: **{score}%**\n"
        f"  Iterations: {iterations}\n"
        f"  Time: {elapsed}\n"
    )
    await ctx.send(channel, summary)

    # Send side-by-side images
    files = []
    if ref and Path(ref).exists():
        files.append(discord.File(ref, filename="reference.png"))
    if final and Path(final).exists():
        files.append(discord.File(final, filename="result.png"))
    if files:
        await channel.send(
            content="**Reference** vs **Result**:",
            files=files,
        )


HANDLERS = {
    "build_from_design": handle_build_from_design_cmd,
}
