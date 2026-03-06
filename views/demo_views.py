"""
demo_views.py — Platform picker for /demo (DemoPlatformView).

The actual _run_demo logic lives outside this module. It is accepted
as a callback (run_demo_fn) to avoid pulling heavy platform imports
into the views layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable, Any

import discord

if TYPE_CHECKING:
    pass


class DemoPlatformView(discord.ui.View):
    """Platform picker buttons for /demo."""

    def __init__(self, owner_id: int, ws_key: str, ws_path: str,
                 run_demo_fn: Callable[..., Awaitable[Any]]):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.ws_key = ws_key
        self.ws_path = ws_path
        self._run_demo = run_demo_fn

    @discord.ui.button(label="Android", style=discord.ButtonStyle.success, emoji="\U0001f4f1")
    async def android(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        await self._run_demo(interaction.channel, self.ws_key, self.ws_path, "android")

    @discord.ui.button(label="iOS", style=discord.ButtonStyle.primary, emoji="\U0001f34e")
    async def ios(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        await self._run_demo(interaction.channel, self.ws_key, self.ws_path, "ios")

    @discord.ui.button(label="Web", style=discord.ButtonStyle.secondary, emoji="\U0001f310")
    async def web(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        await self._run_demo(interaction.channel, self.ws_key, self.ws_path, "web")
