"""helpers/pro_tips.py — Pro tips embed and dismiss view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_context import BotContext


TIPS = [
    (
        "Save often",
        "You don't have direct access to the code or terminal \u2014 "
        "`/save` frequently so you can restore a previous save via `/save list` if something breaks.",
    ),
    (
        "Share screenshots",
        "See a bug? Just paste a screenshot \u2014 the bot can see images. "
        "Add a message like \"fix this\" or send the image alone.",
    ),
    (
        "Debug smarter",
        "Adding a tricky feature? Ask the bot to add debug/log statements, "
        "then share the output so it can fix issues faster.",
    ),
    (
        "Your app has a live database",
        "Use `/data template` to get CSV templates, fill them in with "
        "Google Sheets, then `/data import` to bulk-load rows. "
        "`/data export` to download everything. "
        "You can also just ask the bot to add or update data for you \u2014 no CSV needed.",
    ),
    (
        "Start with web",
        "Use `/demo web` for quick iteration. Once happy, run "
        "`/testflight` (iOS) or `/playstore` (Android) to go native. "
        "Ping `jared.e.tan@gmail.com` for setup help.",
    ),
]


def pro_tip_embed(index: int) -> discord.Embed:
    """Single-tip embed for rotating display after prompts."""
    name, value = TIPS[index % len(TIPS)]
    embed = discord.Embed(title=f"\U0001f4a1 Pro Tip ({index + 1}/{len(TIPS)})", color=0x5865F2)
    embed.add_field(name=name, value=value, inline=False)
    return embed


def all_pro_tips_embed() -> discord.Embed:
    """Full tips embed for /help."""
    embed = discord.Embed(title="\U0001f4a1 Pro Tips", color=0x5865F2)
    for name, value in TIPS:
        embed.add_field(name=name, value=value, inline=False)
    return embed


class ProTipsDismissView(discord.ui.View):
    def __init__(self, ctx: BotContext, user_id: int):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.user_id = user_id

    @discord.ui.button(label="Hide tips", style=discord.ButtonStyle.secondary)
    async def hide_tips(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Only the original user can dismiss tips.", ephemeral=True,
            )
        self.ctx.registry.hide_tips(self.user_id)
        # Edit the message to remove embed, then send ephemeral confirmation
        await interaction.response.edit_message(embed=None, view=None)
        await interaction.followup.send(
            "Tips hidden. Use `/help` to see them again.", ephemeral=True,
        )
