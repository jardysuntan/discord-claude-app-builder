"""
handlers/plan_commands.py — /planapp command handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from views.planapp_views import PlanAppView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_planapp(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        prefill = cmd.raw_cmd or ""
        view = PlanAppView(ctx, channel, user_id, is_admin, prefill)
        await channel.send(
            "**Plan before you build!** Describe your app idea and I'll create a structured plan "
            "with screens, navigation, data model, and features — before writing any code.\n\n"
            "Review the plan, then hit **Build** when you're happy.",
            view=view,
        )


HANDLERS = {
    "planapp": handle_planapp,
}
