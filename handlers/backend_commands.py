"""
handlers/backend_commands.py — Handler for /add-backend command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands.backend import get_backend, run_backend
from views.backend_views import backends_embed, BackendSelectView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_addbackend(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
        return

    # /add-backend <provider> — skip the menu, go straight to provisioning
    if cmd.raw_cmd:
        backend = get_backend(cmd.raw_cmd)
        if backend:
            ws_key, ws_path = ctx.registry.resolve(None, user_id)
            if not ws_path:
                await ctx.send(channel, "❌ No workspace set. Use `/use <workspace>` first.")
                return
            if not ctx.registry.can_access(ws_key, user_id, is_admin):
                await ctx.send(channel, "You don't have access to that workspace.")
                return

            await ctx.send(
                channel,
                f"{backend.emoji} **Provisioning {backend.label}** in **{ws_key}**...",
            )

            async def on_status(msg, fpath=None):
                await ctx.send(channel, msg, file_path=fpath)

            platform = ctx.registry.get_platform(user_id) or "android"

            success = await run_backend(
                backend=backend,
                workspace_key=ws_key,
                workspace_path=ws_path,
                claude=ctx.claude,
                on_status=on_status,
                platform=platform,
            )

            if success:
                await ctx.send(
                    channel,
                    f"✅ **{backend.label}** provisioned in **{ws_key}**! Run `/demo` to test.",
                )
            else:
                await ctx.send(
                    channel,
                    f"⚠️ **{backend.label}** provisioning had build issues. "
                    "Check errors above and try prompting to fix.",
                )
            return

        # Unknown provider name
        await ctx.send(
            channel,
            f"❌ Unknown backend `{cmd.raw_cmd}`. Options: `firebase`, `supabase`.\n"
            "Use `/add-backend` to see all options.",
        )
        return

    # /add-backend (no args) — show menu
    embed = backends_embed()
    view = BackendSelectView(ctx, channel, user_id, is_admin)
    await channel.send(embed=embed, view=view)


HANDLERS = {
    "addbackend": handle_addbackend,
}
