"""
views/planapp_views.py — Plan-app modal, embed, and approve/build buttons.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from commands import planapp
from commands.buildapp import infer_app_name

if TYPE_CHECKING:
    from bot_context import BotContext


# ── Persistent plan storage ──────────────────────────────────────────────────

_PLANS_FILE = Path(__file__).resolve().parent.parent / "app_plans.json"


def _load_plans() -> dict:
    if _PLANS_FILE.exists():
        try:
            with open(_PLANS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _save_plan(user_id: int, plan: dict):
    plans = _load_plans()
    plans[str(user_id)] = plan
    with open(_PLANS_FILE, "w") as f:
        json.dump(plans, f, indent=2)


def get_plan(user_id: int) -> dict | None:
    plans = _load_plans()
    return plans.get(str(user_id))


# ── Discord embed from plan ──────────────────────────────────────────────────

def plan_embed(plan: dict) -> discord.Embed:
    """Build a rich Discord embed from a plan dict."""
    fmt = planapp.format_plan_embed(plan)
    embed = discord.Embed(
        title=fmt["title"],
        description=fmt["summary"],
        color=0x5865F2,  # Discord blurple
    )
    for name, value in fmt["fields"]:
        # Discord embed field value max is 1024 chars
        embed.add_field(name=name, value=value[:1024], inline=False)
    embed.set_footer(text="Review this plan, then tap Build to start — or Edit to refine it.")
    return embed


# ── Modal: enter app description ─────────────────────────────────────────────

class _PlanAppModal(discord.ui.Modal, title="Plan your app"):
    app_name_input = discord.ui.TextInput(
        label="App name (optional)",
        style=discord.TextStyle.short,
        placeholder="e.g. FridgeChef — leave blank to let the AI suggest one",
        required=False,
        max_length=60,
    )
    description = discord.ui.TextInput(
        label="Describe your app idea",
        style=discord.TextStyle.long,
        placeholder="e.g. a meal planner with recipes based on what's in your fridge",
        required=True,
        max_length=4000,
    )

    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool,
                 prefill: str = "", prefill_name: str = ""):
        super().__init__()
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        if prefill:
            self.description.default = prefill[:4000]
        if prefill_name:
            self.app_name_input.default = prefill_name[:60]

    async def on_submit(self, interaction: discord.Interaction):
        desc = self.description.value.strip()
        user_app_name = (self.app_name_input.value or "").strip()

        # Ack the modal immediately (required within 3s), then post a public status message
        await interaction.response.defer()
        status_msg = await self.channel.send(
            "🧠 **Planning your app...**\n"
            "_Analyzing requirements — this usually takes 30-60 seconds._",
        )

        # Background task: periodically edit the status message so the user knows we're still alive
        stop_event = asyncio.Event()

        async def progress_ticker():
            stages = [
                ("🧭", "Designing navigation and screens..."),
                ("🗄️", "Sketching the data model..."),
                ("✨", "Finalizing features and tech stack..."),
                ("⏳", "Almost done — polishing the plan..."),
            ]
            stage_idx = 0
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=12.0)
                    return
                except asyncio.TimeoutError:
                    emoji, text = stages[min(stage_idx, len(stages) - 1)]
                    try:
                        await status_msg.edit(
                            content=f"{emoji} **Planning your app...**\n_{text}_",
                        )
                    except Exception:
                        pass
                    stage_idx += 1

        ticker_task = asyncio.create_task(progress_ticker())

        try:
            plan = await planapp.generate_plan(
                desc, self.ctx.claude,
            )
        finally:
            stop_event.set()
            try:
                await ticker_task
            except Exception:
                pass

        if not plan:
            try:
                await status_msg.edit(
                    content="❌ Could not generate a plan. Try again with a more detailed description.",
                )
            except Exception:
                await self.ctx.send(
                    self.channel,
                    "❌ Could not generate a plan. Try again with a more detailed description.",
                )
            return

        # Apply user-provided app name override if given
        if user_app_name:
            plan["app_name"] = user_app_name

        # Store plan for this user
        _save_plan(self.user_id, plan)

        # Replace status message with a "done" note, then post the plan embed
        try:
            await status_msg.edit(content="✅ **Plan ready!**")
        except Exception:
            pass

        embed = plan_embed(plan)
        view = _PlanActionView(self.ctx, self.channel, self.user_id, self.is_admin, plan)
        await self.channel.send(embed=embed, view=view)


# ── View: button to open the modal ──────────────────────────────────────────

class PlanAppView(discord.ui.View):
    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool, prefill: str = ""):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.prefill = prefill

    @discord.ui.button(label="Describe your app", style=discord.ButtonStyle.success, emoji="🧠")
    async def describe_app(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(
            _PlanAppModal(self.ctx, self.channel, self.user_id, self.is_admin, prefill=self.prefill)
        )


# ── View: approve / edit / rebuild plan ──────────────────────────────────────

class _PlanActionView(discord.ui.View):
    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool, plan: dict):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.plan = plan

    @discord.ui.button(label="Build this app", style=discord.ButtonStyle.success, emoji="🚀")
    async def build_app(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)

        # Disable buttons
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        app_name = self.plan.get("app_name", infer_app_name(
            self.plan.get("_original_description", "MyApp")
        ))
        enriched_desc = planapp.plan_to_buildapp_prompt(self.plan)

        await interaction.response.send_message(
            f"🚀 Building **{app_name}** from plan...", ephemeral=True,
        )

        # Import and trigger buildapp
        from commands.buildapp import handle_buildapp as ba_handle

        async def ba_status(msg, fpath=None):
            await self.ctx.send(self.channel, msg, file_path=fpath)

        # handle_buildapp auto-switches the default workspace right after
        # create_kmp_project, so callers don't need a post-build set_default.
        await ba_handle(
            enriched_desc,
            self.ctx.registry,
            self.ctx.claude,
            ba_status,
            is_admin=self.is_admin,
            owner_id=self.user_id,
            app_name=app_name,
        )

    @discord.ui.button(label="Refine plan", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def replan(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(
            _RefinePlanModal(self.ctx, self.channel, self.user_id, self.is_admin, self.plan)
        )


# ── Modal: refine an existing plan with small changes ───────────────────────

class _RefinePlanModal(discord.ui.Modal, title="Refine your plan"):
    changes = discord.ui.TextInput(
        label="What would you like to change?",
        style=discord.TextStyle.long,
        placeholder=(
            "e.g. 'add a Settings screen', 'remove the spin wheel game', "
            "'use Ktor instead of the Supabase SDK', 'rename app to MyTrip'"
        ),
        required=True,
        max_length=2000,
    )

    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool, plan: dict):
        super().__init__()
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.plan = plan

    async def on_submit(self, interaction: discord.Interaction):
        changes = self.changes.value.strip()

        await interaction.response.defer()
        status_msg = await self.channel.send(
            f"✏️ **Refining the plan...**\n_Applying: {changes[:200]}_",
        )

        stop_event = asyncio.Event()

        async def progress_ticker():
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=15.0)
                    return
                except asyncio.TimeoutError:
                    try:
                        await status_msg.edit(
                            content=f"⏳ **Still refining...**\n_Applying: {changes[:200]}_",
                        )
                    except Exception:
                        pass

        ticker_task = asyncio.create_task(progress_ticker())

        try:
            updated = await planapp.refine_plan(self.plan, changes, self.ctx.claude)
        finally:
            stop_event.set()
            try:
                await ticker_task
            except Exception:
                pass

        if not updated:
            try:
                await status_msg.edit(
                    content="❌ Could not refine the plan. Try describing the changes differently.",
                )
            except Exception:
                await self.ctx.send(
                    self.channel,
                    "❌ Could not refine the plan. Try describing the changes differently.",
                )
            return

        _save_plan(self.user_id, updated)

        try:
            await status_msg.edit(content="✅ **Plan updated!**")
        except Exception:
            pass

        embed = plan_embed(updated)
        view = _PlanActionView(self.ctx, self.channel, self.user_id, self.is_admin, updated)
        await self.channel.send(embed=embed, view=view)
