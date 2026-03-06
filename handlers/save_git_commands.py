"""
handlers/save_git_commands.py — Save and Git commands
(save, gitstatus, diff, commit, undo, gitlog, branch, stash, pr, repo, mirror).

Extracted from bot.py lines 2458-2574.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands import git_cmd
from views.save_views import SaveConfirmView, SaveListView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_save(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        match cmd.sub:
            case "list":
                text, saves = await git_cmd.handle_save_list(ws_path)
                if saves and len(saves) > 1:
                    view = SaveListView(user_id, ws_path, saves)
                    view.message = await channel.send(text, view=view)
                else:
                    await ctx.send(channel, text)
            case "undo":
                await ctx.send(channel, await git_cmd.handle_save_undo(ws_path))
            case "redo":
                await ctx.send(channel, await git_cmd.handle_save_redo(ws_path))
            case "github":
                await ctx.send(channel, await git_cmd.handle_save_github(ws_path, ws_key))
            case _:
                if cmd.raw_cmd:
                    # Custom message: save directly, no preview
                    await ctx.send(channel, await git_cmd.handle_save(
                        ws_path, ws_key, claude=ctx.claude, custom_msg=cmd.raw_cmd))
                else:
                    # No message: preview with confirm/edit buttons
                    result = await git_cmd.prepare_save(ws_path, ws_key, claude=ctx.claude)
                    if isinstance(result, str):
                        await ctx.send(channel, result)
                    else:
                        num, description = result
                        view = SaveConfirmView(ws_path, user_id, num, description)
                        preview = (
                            f"💾 **Save {num}** — {description}\n"
                            f"-# Click Save to confirm, or edit the description first."
                        )
                        view.message = await channel.send(preview, view=view)


async def handle_gitstatus(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, await git_cmd.handle_status(ws_path, ws_key))


async def handle_diff(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        full = cmd.sub == "full" if cmd.sub else False
        await ctx.send(channel, await git_cmd.handle_diff(ws_path, full))


async def handle_commit(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        result = await git_cmd.handle_commit(
            ws_path, ws_key, message=cmd.raw_cmd, claude=ctx.claude, auto_push=True)
        await ctx.send(channel, result)


async def handle_undo(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, await git_cmd.handle_undo(ws_path))


async def handle_gitlog(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        count = int(cmd.raw_cmd) if cmd.raw_cmd and cmd.raw_cmd.isdigit() else 10
        await ctx.send(channel, await git_cmd.handle_log(ws_path, count))


async def handle_branch(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, await git_cmd.handle_branch(ws_path, cmd.raw_cmd))


async def handle_stash(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, await git_cmd.handle_stash(ws_path, pop=(cmd.sub == "pop")))


async def handle_pr(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, await git_cmd.handle_pr(
            ws_path, ws_key, title=cmd.raw_cmd, claude=ctx.claude))


async def handle_repo(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, await git_cmd.handle_repo(
            ws_path, ws_key, sub=cmd.sub, arg=cmd.arg))


async def handle_mirror(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not is_admin:
        await ctx.send(channel, "🔒 `/mirror` is admin-only.")
    else:
        from commands.scrcpy import handle_mirror as _mirror
        await ctx.send(channel, await _mirror(cmd.sub or "start"))


HANDLERS = {
    "save": handle_save,
    "gitstatus": handle_gitstatus,
    "diff": handle_diff,
    "commit": handle_commit,
    "undo": handle_undo,
    "gitlog": handle_gitlog,
    "branch": handle_branch,
    "stash": handle_stash,
    "pr": handle_pr,
    "repo": handle_repo,
    "mirror": handle_mirror,
}
