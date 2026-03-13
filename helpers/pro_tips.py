"""helpers/pro_tips.py — Pro tips embed and dismiss view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_context import BotContext


def pro_tips_embed() -> discord.Embed:
    embed = discord.Embed(title="\U0001f4a1 Pro Tips", color=0x5865F2)
    embed.add_field(
        name="Save often",
        value=(
            "You don't have direct access to the code or terminal \u2014 "
            "`/save` frequently so you can restore a previous save via `/save list` if something breaks."
        ),
        inline=False,
    )
    embed.add_field(
        name="Share screenshots",
        value=(
            "See a bug? Just paste a screenshot \u2014 the bot can see images. "
            "Add a message like \"fix this\" or send the image alone."
        ),
        inline=False,
    )
    embed.add_field(
        name="Debug smarter",
        value=(
            "Adding a tricky feature? Ask the bot to add debug/log statements, "
            "then share the output so it can fix issues faster."
        ),
        inline=False,
    )
    embed.add_field(
        name="Your app has a live database",
        value=(
            "Use `/data template` to get CSV templates, fill them in with "
            "Google Sheets, then `/data import` to bulk-load rows. "
            "`/data export` to download everything. "
            "You can also just ask the bot to add or update data for you \u2014 no CSV needed."
        ),
        inline=False,
    )
    embed.add_field(
        name="Start with web",
        value=(
            "Use `/demo web` for quick iteration. Once happy, run "
            "`/testflight` (iOS) or `/playstore` (Android) to go native. "
            "Ping `jared.e.tan@gmail.com` for setup help."
        ),
        inline=False,
    )
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
