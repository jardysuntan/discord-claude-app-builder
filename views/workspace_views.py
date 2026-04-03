"""
workspace_views.py — Workspace management views (ConfirmDeleteView,
WorkspaceSelectorView, WorkspaceButton).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_context import BotContext


class ConfirmDeleteView(discord.ui.View):
    """Confirmation buttons for /remove <workspace>."""

    def __init__(self, ctx: BotContext, ws_key: str, ws_path: str, owner_id: int):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.owner_id = owner_id

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        import shutil as _shutil
        try:
            _shutil.rmtree(self.ws_path)
        except Exception as e:
            return await interaction.response.edit_message(
                content=f"Failed to delete `{self.ws_path}`: {e}", view=None)
        self.ctx.registry.remove(self.ws_key)
        await interaction.response.edit_message(
            content=f"Deleted **{self.ws_key}** (`{self.ws_path}`).", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.edit_message(content="Cancelled.", view=None)

    async def on_timeout(self):
        pass


_PAGE_SIZE = 20  # max workspace buttons per page (leave room for nav row)


class WorkspaceSelectorView(discord.ui.View):
    """Shows workspace buttons for switching, with pagination."""

    def __init__(self, ctx: BotContext, owner_id: int, keys: list[str], page: int = 0):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.owner_id = owner_id
        self.all_keys = keys
        self.page = page
        self.footer_message = None  # set after footer is sent, so button can edit it
        self.total_pages = max(1, (len(keys) + _PAGE_SIZE - 1) // _PAGE_SIZE)

        current = ctx.registry.get_default(owner_id)
        start = page * _PAGE_SIZE
        page_keys = keys[start : start + _PAGE_SIZE]
        for key in page_keys:
            style = discord.ButtonStyle.primary if key == current else discord.ButtonStyle.secondary
            self.add_item(WorkspaceButton(ctx, key, style, owner_id))

        # Add nav buttons if more than one page
        if self.total_pages > 1:
            self.add_item(_PrevPageButton(ctx, owner_id, keys, page, disabled=page == 0))
            self.add_item(_PageIndicatorButton(page, self.total_pages))
            self.add_item(_NextPageButton(ctx, owner_id, keys, page, disabled=page >= self.total_pages - 1))


class _PrevPageButton(discord.ui.Button):
    def __init__(self, ctx: BotContext, owner_id: int, keys: list[str], page: int, disabled: bool):
        super().__init__(label="\u25c0 Prev", style=discord.ButtonStyle.secondary, disabled=disabled, row=4)
        self.ctx = ctx
        self._owner_id = owner_id
        self._keys = keys
        self._page = page

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        new_view = WorkspaceSelectorView(self.ctx, self._owner_id, self._keys, self._page - 1)
        label = f"**Your Apps** (page {self._page}/{new_view.total_pages})"
        await interaction.response.edit_message(content=label, view=new_view)


class _NextPageButton(discord.ui.Button):
    def __init__(self, ctx: BotContext, owner_id: int, keys: list[str], page: int, disabled: bool):
        super().__init__(label="Next \u25b6", style=discord.ButtonStyle.secondary, disabled=disabled, row=4)
        self.ctx = ctx
        self._owner_id = owner_id
        self._keys = keys
        self._page = page

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        new_view = WorkspaceSelectorView(self.ctx, self._owner_id, self._keys, self._page + 1)
        label = f"**Your Apps** (page {self._page + 2}/{new_view.total_pages})"
        await interaction.response.edit_message(content=label, view=new_view)


class _PageIndicatorButton(discord.ui.Button):
    def __init__(self, page: int, total_pages: int):
        super().__init__(label=f"{page + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=4)


class WorkspaceButton(discord.ui.Button):
    """Individual workspace button."""

    def __init__(self, ctx: BotContext, ws_key: str, style: discord.ButtonStyle, owner_id: int):
        super().__init__(label=ws_key, style=style)
        self.ctx = ctx
        self.ws_key = ws_key
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.ctx.registry.set_default(self.owner_id, self.ws_key)
        await interaction.response.edit_message(
            content=f"Switched to **{self.ws_key}**.", view=None)
        # Update the workspace footer below if it exists
        if self.view and hasattr(self.view, 'footer_message') and self.view.footer_message:
            try:
                await self.view.footer_message.edit(content=f"\U0001f4c2 workspace: **{self.ws_key}**")
            except Exception:
                pass
        # Show incomplete Play Store checklist if exists
        # Lazy imports to avoid circular deps
        from commands.playstore_state import PlayStoreState
        _btn_ws_path = self.ctx.registry.get_path(self.ws_key)
        if _btn_ws_path and PlayStoreState.exists(_btn_ws_path):
            _btn_state = PlayStoreState.load(_btn_ws_path)
            if not _btn_state.all_done():
                from platforms import AndroidPlatform as _AP3
                from views.playstore_views import PlayStoreChecklistView, _playstore_checklist_embed
                _btn_pkg = _AP3.parse_app_id(_btn_ws_path) or ""
                _btn_app = self.ws_key.replace("-", " ").replace("_", " ").title()
                _btn_view = PlayStoreChecklistView(
                    self.ctx, self.owner_id, self.ws_key, _btn_ws_path, _btn_app, _btn_pkg,
                )
                await interaction.channel.send(
                    embed=_playstore_checklist_embed(
                        self.ws_key, _btn_app, _btn_pkg, _btn_view.state,
                    ),
                    view=_btn_view,
                )
