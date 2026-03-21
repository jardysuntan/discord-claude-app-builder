"""
views/prompt_suggest_views.py — Discord buttons for prompt suggestions.
[Use suggested] / [Use original]
"""

from __future__ import annotations

import discord


class PromptSuggestView(discord.ui.View):
    """Two-button view: accept the suggested prompt or keep the original."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.choice: str | None = None  # "suggested" or "original"

    @discord.ui.button(label="Use suggested", style=discord.ButtonStyle.primary)
    async def use_suggested(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your request.", ephemeral=True)
        self.choice = "suggested"
        self.stop()
        await interaction.response.edit_message(
            content=interaction.message.content + "\n✅ Using suggested prompt.", view=None,
        )

    @discord.ui.button(label="Use original", style=discord.ButtonStyle.secondary)
    async def use_original(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your request.", ephemeral=True)
        self.choice = "original"
        self.stop()
        await interaction.response.edit_message(
            content=interaction.message.content + "\n✅ Using original prompt.", view=None,
        )
