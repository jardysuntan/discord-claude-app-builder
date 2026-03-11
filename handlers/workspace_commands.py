"""
handlers/workspace_commands.py — Workspace management commands
(help, ls, create, deleteapp).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from commands.create import create_kmp_project
from helpers.ui_helpers import help_text
from helpers.pro_tips import pro_tips_embed
from views.workspace_views import ConfirmDeleteView, WorkspaceSelectorView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_help(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    await ctx.send(channel, help_text(is_admin))
    await channel.send(embed=pro_tips_embed())


async def handle_ls(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    user_email = ctx.allowlist.get_email(user_id) if not is_admin else None
    keys = ctx.registry.list_keys(owner_id=None if is_admin else user_id, user_email=user_email)
    if not keys:
        await ctx.send(channel, "No workspaces.")
        return

    # Split into owned vs shared
    owned = [k for k in keys if is_admin or ctx.registry.is_owner(k, user_id)]
    shared = [k for k in keys if k not in owned]

    # ── Owned workspaces (buttons) ──
    if owned:
        view = WorkspaceSelectorView(ctx, user_id, owned)
        await channel.send("**Your Apps:**", view=view)
        cmd._selector_view = view  # type: ignore[attr-defined]

    # ── Shared workspaces (embed + buttons) ──
    if shared:
        shared_view = WorkspaceSelectorView(ctx, user_id, shared)
        embed = discord.Embed(title="Shared with you", color=0x5865F2)
        for key in shared:
            owner_id_ws = ctx.registry.get_owner(key)
            owner_name = ctx.allowlist.get_display_name(owner_id_ws) if owner_id_ws else "Unknown"
            collabs = ctx.registry.get_collaborators(key)
            others = [c for c in collabs if c.get("user_id") != user_id]
            lines = [f"\U0001f451 **{owner_name}** (owner)"]
            for c in others:
                name = c.get("name", "?")
                email = c.get("email", "")
                uid = c.get("user_id")
                discord_name = ctx.allowlist.get_display_name(uid) if uid else None
                detail = f"\U0001f464 {name}"
                if discord_name and discord_name != name:
                    detail += f" ({discord_name})"
                if email:
                    detail += f" \u00b7 {email}"
                lines.append(detail)
            embed.add_field(name=key, value="\n".join(lines), inline=False)
        await channel.send(embed=embed, view=shared_view)
        if not owned:
            cmd._selector_view = shared_view  # type: ignore[attr-defined]

    # ── Show collab details for admin's owned workspaces ──
    if owned and (is_admin or any(ctx.registry.get_collaborators(k) for k in owned)):
        collab_lines = []
        for key in owned:
            collabs = ctx.registry.get_collaborators(key)
            if collabs:
                names = ", ".join(
                    f"{c.get('name', '?')} ({c.get('email', '')})" for c in collabs
                )
                collab_lines.append(f"**{key}** \u2014 {names}")
        if collab_lines:
            embed = discord.Embed(
                title="Collaborators",
                description="\n".join(collab_lines),
                color=0x57F287,
            )
            await channel.send(embed=embed)


async def handle_create(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    elif not cmd.app_name:
        await ctx.send(channel, "Usage: `/create <AppName>`")
    else:
        result = await create_kmp_project(cmd.app_name, ctx.registry, owner_id=user_id)
        await ctx.send(channel, result.message)
        if result.success and result.slug:
            ctx.registry.set_default(user_id, result.slug)
            await ctx.send(channel, f"\U0001f4c2 Switched to **{result.slug}**")


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
        elif not is_admin and not ctx.registry.is_owner(ws_key, user_id):
            await ctx.send(channel, "Only the workspace owner can delete it.")
        else:
            view = ConfirmDeleteView(ctx, ws_key, ws_path, user_id)
            await channel.send(
                f"Delete **{ws_key}** (`{ws_path}`)?\nThis removes all files permanently.",
                view=view,
            )


HANDLERS = {
    "help": handle_help,
    "ls": handle_ls,
    "deleteapp": handle_deleteapp,
}
