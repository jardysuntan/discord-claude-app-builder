"""
views/planapp_views.py — Plan-app modal, embed, and approve/build buttons.
"""

from __future__ import annotations

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
    description = discord.ui.TextInput(
        label="Describe your app idea",
        style=discord.TextStyle.long,
        placeholder="e.g. a meal planner that suggests recipes based on what's in your fridge, with a grocery list and calorie tracker",
        required=True,
        max_length=4000,
    )

    def __init__(self, ctx: BotContext, channel, user_id: int, is_admin: bool, prefill: str = ""):
        super().__init__()
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        if prefill:
            self.description.default = prefill[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        desc = self.description.value.strip()
        await interaction.response.send_message(
            "🧠 Planning your app — this takes about 30 seconds...", ephemeral=True,
        )

        plan = await planapp.generate_plan(
            desc, self.ctx.claude,
        )

        if not plan:
            await self.ctx.send(
                self.channel,
                "❌ Could not generate a plan. Try again with a more detailed description.",
            )
            return

        # Store plan for this user
        _save_plan(self.user_id, plan)

        # Send the plan embed with action buttons
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
            _PlanAppModal(self.ctx, self.channel, self.user_id, self.is_admin, self.prefill)
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

        slug = await ba_handle(
            enriched_desc,
            self.ctx.registry,
            self.ctx.claude,
            ba_status,
            is_admin=self.is_admin,
            owner_id=self.user_id,
            app_name=app_name,
        )
        if slug:
            self.ctx.registry.set_default(self.user_id, slug)
            await self.ctx.send(self.channel, f"📂 Switched to **{slug}**")

    @discord.ui.button(label="Re-plan", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def replan(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        original_desc = self.plan.get("_original_description", "")
        await interaction.response.send_modal(
            _PlanAppModal(self.ctx, self.channel, self.user_id, self.is_admin, original_desc)
        )
