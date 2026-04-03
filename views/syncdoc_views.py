"""
views/syncdoc_views.py — Confirmation UI for /syncdoc.

Shows a change summary with Confirm / Cancel buttons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable

import discord

if TYPE_CHECKING:
    from helpers.google_docs_sync import SyncPlan


class SyncConfirmView(discord.ui.View):
    """Confirm / Cancel buttons for a Google Doc sync operation."""

    def __init__(
        self,
        plan: SyncPlan,
        schema: str,
        schema_sql: str,
        tables: dict,
        owner_id: int,
        on_status: Callable[[str], Awaitable[None]],
    ):
        super().__init__(timeout=120)
        self.plan = plan
        self.schema = schema
        self.schema_sql = schema_sql
        self.tables = tables
        self.owner_id = owner_id
        self.on_status = on_status

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="\u2705")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "Only the person who ran `/syncdoc` can confirm.", ephemeral=True
            )

        await interaction.response.edit_message(
            content="Applying changes\u2026", view=None
        )

        from helpers.google_docs_sync import execute_sync

        ok, result = await execute_sync(
            self.plan, self.schema, self.schema_sql, self.tables
        )

        if ok:
            await self.on_status(f"\u2705 **Sync complete!** {result}")
        else:
            await self.on_status(f"\u26a0\ufe0f **Sync finished with errors:**\n{result}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="\u274c")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "Only the person who ran `/syncdoc` can cancel.", ephemeral=True
            )

        await interaction.response.edit_message(
            content="Sync cancelled \u2014 no changes were made.", view=None
        )

    async def on_timeout(self):
        """Auto-cancel after 120s of inactivity."""
        try:
            await self.on_status("Sync timed out \u2014 no changes were made. Run `/syncdoc` again to retry.")
        except Exception:
            pass
