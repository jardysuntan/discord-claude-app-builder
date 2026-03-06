"""
buildapp_views.py — Build-app modal and view (_BuildAppModal, _BuildAppView).

These use ctx.client.wait_for, ctx.interview_pending, and the buildapp module.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from commands import buildapp
from views.interview_views import SkipDataInterviewView

if TYPE_CHECKING:
    from bot_context import BotContext


class _BuildAppModal(discord.ui.Modal, title="Build a new app"):
    app_name = discord.ui.TextInput(
        label="App name",
        placeholder="e.g. WorkoutTracker",
        required=True,
        max_length=50,
    )
    description = discord.ui.TextInput(
        label="Describe your app",
        style=discord.TextStyle.long,
        placeholder="e.g. a workout tracker with exercise categories, sets/reps logging, and a rest timer",
        required=True,
        max_length=500,
    )

    def __init__(self, ctx: BotContext, channel, user_id, is_admin, prefill_desc=""):
        super().__init__()
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        if prefill_desc:
            self.description.default = prefill_desc
            self.app_name.default = buildapp.infer_app_name(prefill_desc)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.app_name.value.strip()
        desc = self.description.value.strip()
        await interaction.response.send_message(
            f"\U0001f680 Building **{name}**...", ephemeral=True,
        )

        async def ba_status(msg, fpath=None):
            await self.ctx.send(self.channel, msg, file_path=fpath)

        async def ba_ask(question: str) -> str | None:
            view = SkipDataInterviewView()
            q_msg = await self.channel.send(question, view=view)
            pair = (self.channel.id, self.user_id)
            self.ctx.interview_pending.add(pair)

            def check(m: discord.Message) -> bool:
                return (
                    m.channel.id == self.channel.id
                    and m.author.id == self.user_id
                    and not m.content.startswith("/")
                    and not m.content.startswith("@")
                )

            try:
                wait_msg = asyncio.ensure_future(
                    self.ctx.client.wait_for("message", check=check, timeout=120)
                )
                wait_skip = asyncio.ensure_future(view.wait())
                done, pending = await asyncio.wait(
                    {wait_msg, wait_skip}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()

                if wait_msg in done:
                    reply = wait_msg.result()
                    view.stop()
                    await q_msg.edit(view=None)
                    return reply.content.strip() or None
                await q_msg.edit(view=None)
                return None
            except asyncio.TimeoutError:
                await q_msg.edit(view=None)
                return None
            finally:
                self.ctx.interview_pending.discard(pair)

        slug = await buildapp.handle_buildapp(
            desc, self.ctx.registry, self.ctx.claude, ba_status,
            on_ask=ba_ask, is_admin=self.is_admin, owner_id=self.user_id,
            app_name=name,
        )
        if slug:
            self.ctx.registry.set_default(self.user_id, slug)
            await self.ctx.send(self.channel, f"\U0001f4c2 Switched to **{slug}**")


class _BuildAppView(discord.ui.View):
    def __init__(self, ctx: BotContext, channel, user_id, is_admin, prefill_desc=""):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.prefill_desc = prefill_desc

    @discord.ui.button(label="Get started", style=discord.ButtonStyle.success, emoji="\U0001f680")
    async def get_started(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(
            _BuildAppModal(self.ctx, self.channel, self.user_id, self.is_admin, self.prefill_desc)
        )
