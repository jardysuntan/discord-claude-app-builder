"""helpers/welcome.py — Rich welcome embed and view for new users."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from helpers.ui_helpers import help_text

if TYPE_CHECKING:
    from bot_context import BotContext


def welcome_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Welcome to discord-claude-bridge!",
        description="Build real apps from chat — no coding needed.",
        color=0x5865F2,
    )
    embed.add_field(
        name="Get started",
        value="Send `/buildapp a pomodoro timer` to create your first app",
        inline=False,
    )
    embed.add_field(
        name="Chat with your app",
        value="Just send a message to iterate. Paste screenshots to show bugs.",
        inline=False,
    )
    embed.add_field(
        name="Preview",
        value="`/demo web` to see it live in your browser",
        inline=False,
    )
    embed.add_field(
        name="Save your work",
        value="`/save` early, `/save` often",
        inline=False,
    )
    return embed


class WelcomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="See all commands", style=discord.ButtonStyle.secondary)
    async def see_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(help_text(is_admin=False), ephemeral=True)
