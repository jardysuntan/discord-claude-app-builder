"""
save_views.py — Save-related views (EditSaveDescriptionModal, SaveConfirmView,
SaveListView, ConfirmLoadButton, CancelLoadButton).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from commands import git_cmd

if TYPE_CHECKING:
    pass


class EditSaveDescriptionModal(discord.ui.Modal, title="Edit save description"):
    """Modal to edit the auto-generated save description before committing."""

    description_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.long,
        max_length=500,
        placeholder="e.g. added dark mode toggle",
    )

    def __init__(self, view: "SaveConfirmView"):
        super().__init__()
        self.save_view = view
        self.description_input.default = view.description

    async def on_submit(self, interaction: discord.Interaction):
        new_desc = self.description_input.value.strip()[:500]
        self.save_view.stop()
        result = await git_cmd.commit_save(
            self.save_view.ws_path, self.save_view.save_number, new_desc,
        )
        await interaction.response.edit_message(content=result, view=None)


class SaveConfirmView(discord.ui.View):
    """Preview save description with Save / Edit buttons."""

    def __init__(self, ws_path: str, owner_id: int, save_number: int, description: str):
        super().__init__(timeout=60)
        self.ws_path = ws_path
        self.owner_id = owner_id
        self.save_number = save_number
        self.description = description

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        result = await git_cmd.commit_save(self.ws_path, self.save_number, self.description)
        await interaction.response.edit_message(content=result, view=None)

    @discord.ui.button(label="Edit description", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(EditSaveDescriptionModal(self))

    async def on_timeout(self):
        # Unstage changes so nothing is left half-committed
        await git_cmd._git(["reset", "HEAD"], self.ws_path)
        try:
            await self.message.edit(
                content="\u23f0 Save cancelled (timed out). Your changes are still there \u2014 run `/save` again.",
                view=None,
            )
        except Exception:
            pass


class SaveListView(discord.ui.View):
    """Save history with a dropdown to load any previous save."""

    def __init__(self, owner_id: int, ws_path: str, saves: list[tuple[int, str, str]]):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.ws_path = ws_path
        self.selected_num = None
        # Build select options (max 25 in a Select)
        select = discord.ui.Select(
            placeholder="Load a previous save\u2026",
            min_values=1, max_values=1,
        )
        for num, desc, date in saves[:25]:
            rel = git_cmd._relative_date(date)
            label = f"Save {num}"
            select.append_option(discord.SelectOption(
                label=label,
                description=f"{desc[:50]} \u2014 {rel}",
                value=str(num),
            ))
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.selected_num = int(interaction.data["values"][0])
        # Show confirmation
        self.clear_items()
        self.add_item(ConfirmLoadButton(self))
        self.add_item(CancelLoadButton(self))
        await interaction.response.edit_message(
            content=f"\u23ea Load **Save {self.selected_num}**? This creates a new save with that version's files.",
            view=self,
        )

    async def on_timeout(self):
        try:
            await self.message.edit(view=None)
        except Exception:
            pass


class ConfirmLoadButton(discord.ui.Button):
    def __init__(self, parent: SaveListView):
        super().__init__(label="Load", style=discord.ButtonStyle.success)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.parent_view.stop()
        result = await git_cmd.load_save(self.parent_view.ws_path, self.parent_view.selected_num)
        await interaction.response.edit_message(content=result, view=None)


class CancelLoadButton(discord.ui.Button):
    def __init__(self, parent: SaveListView):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.parent_view.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)
