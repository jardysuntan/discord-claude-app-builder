"""
views/backend_views.py — Discord UI for /add-backend command.

Select menu to pick a backend provider, confirmation button to provision it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from commands.backend import (
    list_backends,
    get_backend,
    run_backend,
    Backend,
)

if TYPE_CHECKING:
    from bot_context import BotContext


def backends_embed() -> discord.Embed:
    """Build an embed listing all available backend providers."""
    embed = discord.Embed(
        title="Backend Provisioning",
        description=(
            "Add a production backend to your app with one command — "
            "auth, data persistence, and platform config included."
        ),
        color=0x5865F2,
    )
    for b in list_backends():
        embed.add_field(
            name=f"{b.emoji} {b.label}",
            value=b.description,
            inline=False,
        )
    return embed


class BackendSelectView(discord.ui.View):
    """Dropdown to choose a backend, then a confirm button."""

    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin

        options = [
            discord.SelectOption(
                label=b.label,
                value=b.key,
                description=b.description[:100],
                emoji=b.emoji,
            )
            for b in list_backends()
        ]
        self.select = discord.ui.Select(
            placeholder="Choose a backend provider...",
            options=options,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )
        selected_key = self.select.values[0]
        backend = get_backend(selected_key)
        if not backend:
            return await interaction.response.send_message(
                "Unknown backend.", ephemeral=True,
            )

        confirm_view = BackendConfirmView(
            self.ctx, self.channel, self.user_id, self.is_admin, backend,
        )
        await interaction.response.send_message(
            f"{backend.emoji} **{backend.label}**\n{backend.description}\n\n"
            "This will add auth + data persistence to your current workspace. Continue?",
            view=confirm_view,
            ephemeral=True,
        )


class BackendConfirmView(discord.ui.View):
    """Confirm / Cancel buttons after selecting a backend."""

    def __init__(
        self,
        ctx: BotContext,
        channel,
        user_id: int,
        is_admin: bool,
        backend: Backend,
    ):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.backend = backend

    @discord.ui.button(label="Provision backend", style=discord.ButtonStyle.success, emoji="✅")
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
            content=f"⏳ Provisioning **{self.backend.label}** in **{ws_key}**...",
            view=None,
        )

        backend = self.backend

        await self.ctx.send(
            self.channel,
            f"{backend.emoji} **Provisioning {backend.label}** in **{ws_key}**...",
        )

        async def on_status(msg, fpath=None):
            await self.ctx.send(self.channel, msg, file_path=fpath)

        platform = self.ctx.registry.get_platform(self.user_id) or "android"

        success = await run_backend(
            backend=backend,
            workspace_key=ws_key,
            workspace_path=ws_path,
            claude=self.ctx.claude,
            on_status=on_status,
            platform=platform,
        )

        if success:
            await self.ctx.send(
                self.channel,
                f"✅ **{backend.label}** provisioned in **{ws_key}**!\n"
                "Run `/demo` to test it out.",
            )
        else:
            await self.ctx.send(
                self.channel,
                f"⚠️ **{backend.label}** provisioning had build issues. "
                "Check the errors above and try prompting to fix.",
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )
        await interaction.response.edit_message(content="Cancelled.", view=None)
