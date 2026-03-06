"""
interview_views.py — Data-interview and cancel-request views
(SkipDataInterviewView, CancelRequestView).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_context import BotContext


class SkipDataInterviewView(discord.ui.View):
    """Single 'Skip' button for the data-modeling interview."""

    def __init__(self):
        super().__init__(timeout=120)
        self.skipped = False

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.skipped = True
        self.stop()
        await interaction.response.defer()


class CancelRequestView(discord.ui.View):
    """Cancel button shown while Claude is processing a request."""

    def __init__(self, ctx: BotContext, owner_id: int, ws_key: str):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.owner_id = owner_id
        self.ws_key = ws_key
        self.cancelled = False

    @discord.ui.button(label="Cancel request", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.cancelled = True
        self.ctx.claude.cancel(self.ws_key)
        self.stop()
        await interaction.response.edit_message(
            content="\U0001f6d1 Request cancelled.", view=None,
        )
