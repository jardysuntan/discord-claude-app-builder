"""
handlers/public_commands.py — Public commands (showcase, tryapp, gallery, done).

These work in both server channels and DMs, for any user.
Extracted from bot.py lines 1694-1730.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_showcase(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    from commands.showcase import handle_showcase as _showcase

    async def pub_status(msg, fpath=None):
        await ctx.send(channel, msg, file_path=fpath)

    if not cmd.workspace:
        return await ctx.send(channel, "Usage: `/showcase <workspace>` or `/showcase gallery`")
    ws_path = ctx.registry.get_path(cmd.workspace)
    if not ws_path:
        return await ctx.send(channel, f"❌ App `{cmd.workspace}` not found.")
    await _showcase(cmd.workspace, ws_path, pub_status)


async def handle_tryapp(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    from commands.showcase import handle_tryapp as _tryapp

    async def pub_status(msg, fpath=None):
        await ctx.send(channel, msg, file_path=fpath)

    if not cmd.workspace:
        return await ctx.send(channel, "Usage: `/tryapp <workspace>`")
    ws_path = ctx.registry.get_path(cmd.workspace)
    if not ws_path:
        return await ctx.send(channel, f"❌ App `{cmd.workspace}` not found.")
    await _tryapp(
        cmd.workspace, ws_path,
        user_id, "",  # display_name not available here — caller should set it
        pub_status,
    )


async def handle_gallery(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    from commands.showcase import handle_gallery as _gallery

    async def pub_status(msg, fpath=None):
        await ctx.send(channel, msg, file_path=fpath)

    await _gallery(pub_status)


async def handle_done(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    from commands.showcase import handle_done as _done

    result = await _done(user_id)
    await ctx.send(channel, result)


HANDLERS = {
    "showcase": handle_showcase,
    "tryapp": handle_tryapp,
    "gallery": handle_gallery,
    "done": handle_done,
}
