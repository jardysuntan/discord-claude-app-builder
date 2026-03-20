"""
views/integrate_views.py — Discord UI for /integrate command.

Select menu to pick an integration, confirmation button to apply it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from commands.integrate import (
    list_integrations,
    get_integration,
    run_integration,
    Integration,
)

if TYPE_CHECKING:
    from bot_context import BotContext


def integrations_embed() -> discord.Embed:
    """Build an embed listing all available integrations."""
    embed = discord.Embed(
        title="Quick-Add Integrations",
        description="Add a pre-configured integration to your app with one command.",
        color=0x5865F2,
    )
    for integ in list_integrations():
        embed.add_field(
            name=f"{integ.emoji} {integ.label}",
            value=integ.description,
            inline=False,
        )
    return embed


class IntegrationSelectView(discord.ui.View):
    """Dropdown to choose an integration, then a confirm button."""

    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.selected_key: str | None = None

        options = [
            discord.SelectOption(
                label=integ.label,
                value=integ.key,
                description=integ.description[:100],
                emoji=integ.emoji,
            )
            for integ in list_integrations()
        ]
        self.select = discord.ui.Select(
            placeholder="Choose an integration...",
            options=options,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )
        self.selected_key = self.select.values[0]
        integ = get_integration(self.selected_key)
        if not integ:
            return await interaction.response.send_message(
                "Unknown integration.", ephemeral=True,
            )

        confirm_view = IntegrationConfirmView(
            self.ctx, self.channel, self.user_id, self.is_admin, integ,
        )
        await interaction.response.send_message(
            f"{integ.emoji} **{integ.label}**\n{integ.description}\n\n"
            "This will modify your current workspace. Continue?",
            view=confirm_view,
            ephemeral=True,
        )


class IntegrationConfirmView(discord.ui.View):
    """Confirm / Cancel buttons after selecting an integration."""

    def __init__(
        self,
        ctx: BotContext,
        channel,
        user_id: int,
        is_admin: bool,
        integration: Integration,
    ):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.integration = integration

    @discord.ui.button(label="Add to my app", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )

        ws_key, ws_path = self.ctx.registry.resolve(None, self.user_id)
        if not ws_path:
            return await interaction.response.send_message(
                "No workspace selected. Use `/use <workspace>` first.", ephemeral=True,
            )
        if not self.ctx.registry.can_access(ws_key, self.user_id, self.is_admin):
            return await interaction.response.send_message(
                "You don't have access to that workspace.", ephemeral=True,
            )

        await interaction.response.edit_message(
            content=f"⏳ Adding **{self.integration.label}** to **{ws_key}**...",
            view=None,
        )

        integ = self.integration

        await self.ctx.send(
            self.channel,
            f"{integ.emoji} **Adding {integ.label}** to **{ws_key}**...",
        )

        async def on_status(msg, fpath=None):
            await self.ctx.send(self.channel, msg, file_path=fpath)

        platform = self.ctx.registry.get_platform(self.user_id) or "android"

        success = await run_integration(
            integration=integ,
            workspace_key=ws_key,
            workspace_path=ws_path,
            claude=self.ctx.claude,
            on_status=on_status,
            platform=platform,
        )

        if success:
            await self.ctx.send(
                self.channel,
                f"✅ **{integ.label}** has been added to **{ws_key}**!\n"
                "Run `/demo` to test it out.",
            )
        else:
            await self.ctx.send(
                self.channel,
                f"⚠️ **{integ.label}** integration had build issues. "
                "Check the errors above and try prompting to fix them.",
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )
        await interaction.response.edit_message(content="Cancelled.", view=None)
