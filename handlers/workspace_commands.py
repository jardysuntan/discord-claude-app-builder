"""
handlers/workspace_commands.py — Workspace management commands
(help, ls, use, where, create, deleteapp, rename).

Extracted from bot.py lines 1908-2000.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands.create import create_kmp_project
from commands.playstore_state import PlayStoreState
from helpers.ui_helpers import help_text
from views.workspace_views import ConfirmDeleteView, WorkspaceSelectorView
from views.playstore_views import PlayStoreChecklistView, _playstore_checklist_embed

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_help(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    await ctx.send(channel, help_text(is_admin))


async def handle_ls(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    keys = ctx.registry.list_keys(owner_id=None if is_admin else user_id)
    if keys:
        view = WorkspaceSelectorView(ctx, user_id, keys)
        await channel.send("**Workspaces:**", view=view)
        # Return the view so the dispatch layer can link it to the footer
        # We store it on cmd as a side-channel (the dispatch layer checks for it)
        cmd._selector_view = view  # type: ignore[attr-defined]
    else:
        await ctx.send(channel, "No workspaces.")


async def handle_use(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not cmd.workspace:
        await ctx.send(channel, "Usage: `/use <workspace>`")
    elif not ctx.registry.exists(cmd.workspace):
        await ctx.send(channel, f"❌ Unknown: `{cmd.workspace}`")
    elif not ctx.registry.can_access(cmd.workspace, user_id, is_admin):
        await ctx.send(channel, "You don't have access to that workspace.")
    elif ctx.registry.set_default(user_id, cmd.workspace):
        await ctx.send(channel, f"✅ Default → **{cmd.workspace}**")
        # Show incomplete Play Store checklist if exists
        _use_ws_path = ctx.registry.get_path(cmd.workspace)
        if _use_ws_path and PlayStoreState.exists(_use_ws_path):
            _use_state = PlayStoreState.load(_use_ws_path)
            if not _use_state.all_done():
                from platforms import AndroidPlatform as _AP2
                _use_pkg = _AP2.parse_app_id(_use_ws_path) or ""
                _use_app = cmd.workspace.replace("-", " ").replace("_", " ").title()
                _use_view = PlayStoreChecklistView(
                    ctx, user_id, cmd.workspace, _use_ws_path, _use_app, _use_pkg,
                )
                await channel.send(
                    embed=_playstore_checklist_embed(
                        cmd.workspace, _use_app, _use_pkg, _use_view.state,
                    ),
                    view=_use_view,
                )
    else:
        await ctx.send(channel, "❌ Could not set default.")


async def handle_where(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws = ctx.registry.get_default(user_id)
    if ws:
        await ctx.send(channel, f"📂 **{ws}** → `{ctx.registry.get_path(ws)}`")
    else:
        await ctx.send(channel, "No default set.")


async def handle_create(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not cmd.app_name:
        await ctx.send(channel, "Usage: `/create <AppName>`")
    else:
        result = await create_kmp_project(cmd.app_name, ctx.registry, owner_id=user_id)
        await ctx.send(channel, result.message)


async def handle_deleteapp(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not cmd.workspace:
        await ctx.send(channel, "Usage: `/remove <workspace>`")
    else:
        ws_key = cmd.workspace.lower()
        ws_path = ctx.registry.get_path(ws_key)
        if not ws_path:
            await ctx.send(channel, f"❌ Unknown workspace: `{ws_key}`")
        elif not ctx.registry.can_access(ws_key, user_id, is_admin):
            await ctx.send(channel, "You don't have access to that workspace.")
        else:
            view = ConfirmDeleteView(ctx, ws_key, ws_path, user_id)
            await channel.send(
                f"Delete **{ws_key}** (`{ws_path}`)?\nThis removes all files permanently.",
                view=view,
            )


async def handle_rename(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not cmd.workspace or not cmd.arg:
        await ctx.send(channel, "Usage: `/rename <old-name> <new-name>`")
    else:
        old_key = cmd.workspace.lower()
        new_key = cmd.arg.lower()
        if not ctx.registry.get_path(old_key):
            await ctx.send(channel, f"❌ Workspace `{old_key}` not found.")
        elif not ctx.registry.can_access(old_key, user_id, is_admin):
            await ctx.send(channel, "You don't have access to that workspace.")
        elif ctx.registry.get_path(new_key):
            await ctx.send(channel, f"❌ `{new_key}` already exists.")
        elif ctx.registry.rename(old_key, new_key):
            await ctx.send(channel, f"Renamed **{old_key}** → **{new_key}**")
        else:
            await ctx.send(channel, f"❌ Could not rename `{old_key}`.")


HANDLERS = {
    "help": handle_help,
    "ls": handle_ls,
    "use": handle_use,
    "where": handle_where,
    "create": handle_create,
    "deleteapp": handle_deleteapp,
    "rename": handle_rename,
}
