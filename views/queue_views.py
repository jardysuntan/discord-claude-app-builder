"""
queue_views.py — Queue builder views (AddQueueTaskModal, QueueBuilderView).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from commands import queue

if TYPE_CHECKING:
    from bot_context import BotContext


class AddQueueTaskModal(discord.ui.Modal, title="Add a task"):
    """Modal popup with a text field for one queue task."""

    task = discord.ui.TextInput(
        label="Task description",
        style=discord.TextStyle.long,
        max_length=500,
        placeholder="e.g. add dark mode support",
    )

    def __init__(self, view: "QueueBuilderView"):
        super().__init__()
        self.queue_view = view

    async def on_submit(self, interaction: discord.Interaction):
        self.queue_view.tasks.append(self.task.value)
        await interaction.response.edit_message(
            content=self.queue_view.build_message(),
            view=self.queue_view,
        )


class QueueBuilderView(discord.ui.View):
    """Interactive wizard: add tasks one at a time, then start the queue."""

    def __init__(self, ctx: BotContext, owner_id: int, channel, ws_key: str, ws_path: str):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.owner_id = owner_id
        self.channel = channel
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.tasks: list[str] = []

    def build_message(self) -> str:
        count = len(self.tasks)
        label = "task" if count == 1 else "tasks"
        header = f"\U0001f4cb **Queue Builder** \u2014 {count} {label}"
        if not self.tasks:
            return header
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(self.tasks, 1))
        return f"{header}\n{listing}"

    @discord.ui.button(label="Add task", style=discord.ButtonStyle.primary)
    async def add_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(AddQueueTaskModal(self))

    @discord.ui.button(label="Start queue \u25b6\ufe0f", style=discord.ButtonStyle.success)
    async def start_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        if not self.tasks:
            return await interaction.response.send_message("Add at least one task first.", ephemeral=True)
        # Disable buttons and update message
        self.stop()
        await interaction.response.edit_message(
            content=self.build_message() + "\n\n\u25b6\ufe0f *Queue started\u2026*",
            view=None,
        )
        # Kick off the queue
        raw = " --- ".join(self.tasks)

        async def queue_status(msg, fpath=None):
            await self.ctx.send(self.channel, msg, file_path=fpath)

        await queue.handle_queue(
            raw, self.ws_key, self.ws_path, self.ctx.claude, self.ctx.cost_tracker,
            on_status=queue_status, user_id=self.owner_id,
        )

    async def on_timeout(self):
        pass
