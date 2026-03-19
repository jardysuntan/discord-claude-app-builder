"""
handlers/integrate_commands.py — Handler for /integrate command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from commands.integrate import get_integration, run_integration
from views.integrate_views import integrations_embed, IntegrationSelectView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_integrate(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
        return

    # /integrate <name> — skip the menu, go straight to confirmation
    if cmd.raw_cmd:
        integ = get_integration(cmd.raw_cmd)
        if integ:
            ws_key, ws_path = ctx.registry.resolve(None, user_id)
            if not ws_path:
                await ctx.send(channel, "❌ No workspace set. Use `/use <workspace>` first.")
                return
            if not ctx.registry.can_access(ws_key, user_id, is_admin):
                await ctx.send(channel, "You don't have access to that workspace.")
                return

            await ctx.send(
                channel,
                f"{integ.emoji} **Adding {integ.label}** to **{ws_key}**...",
            )

            async def on_status(msg, fpath=None):
                await ctx.send(channel, msg, file_path=fpath)

            platform = ctx.registry.get_platform(user_id) or "android"

            success = await run_integration(
                integration=integ,
                workspace_key=ws_key,
                workspace_path=ws_path,
                claude=ctx.claude,
                on_status=on_status,
                platform=platform,
            )

            if success:
                await ctx.send(
                    channel,
                    f"✅ **{integ.label}** added to **{ws_key}**! Run `/demo` to test.",
                )
            else:
                await ctx.send(
                    channel,
                    f"⚠️ **{integ.label}** integration had build issues. "
                    "Check errors above and try prompting to fix.",
                )
            return

        # Unknown integration name
        await ctx.send(
            channel,
            f"❌ Unknown integration `{cmd.raw_cmd}`. Use `/integrate` to see options.",
        )
        return

    # /integrate (no args) — show menu
    embed = integrations_embed()
    view = IntegrationSelectView(ctx, channel, user_id, is_admin)
    await channel.send(embed=embed, view=view)


HANDLERS = {
    "integrate": handle_integrate,
}
