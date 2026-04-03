"""
views/appraise_views.py — Appraisal report card embed and publish gate view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from commands.appraise import score_emoji, score_color, severity_emoji

if TYPE_CHECKING:
    from bot_context import BotContext


def appraisal_embed(appraisal: dict, platform: str = "both") -> discord.Embed:
    """Build a rich Discord embed report card from an appraisal dict."""
    overall = appraisal.get("overall_score", "warn")
    embed = discord.Embed(
        title=f"{score_emoji(overall)} App Appraisal: {overall.upper()}",
        description=appraisal.get("overall_summary", ""),
        color=score_color(overall),
    )

    # Auto-fixes section (shown first, clearly separated)
    auto_fixes = appraisal.get("auto_fixes", [])
    if auto_fixes:
        fix_lines = []
        for fix in auto_fixes:
            fix_lines.append(f"\U0001f527 **{fix.get('title', '')}**\n{fix.get('detail', '')}")
        embed.add_field(
            name="\u2705 Auto-Fixed",
            value="\n".join(fix_lines)[:1024],
            inline=False,
        )

    # Category fields (skip "Auto-Fixed" category since we show it above)
    for cat in appraisal.get("categories", []):
        if cat.get("name") == "Auto-Fixed":
            continue

        cat_score = cat.get("score", "pass")
        findings = cat.get("findings", [])

        if not findings:
            value = "No issues found"
        else:
            lines = []
            for f in findings:
                sev = severity_emoji(f.get("severity", "info"))
                title = f.get("title", "")
                detail = f.get("detail", "")
                hint = f.get("fix_hint", "")
                entry = f"{sev} **{title}**\n{detail}"
                if hint:
                    entry += f"\n\u2192 *{hint}*"
                lines.append(entry)
            value = "\n\n".join(lines)

        embed.add_field(
            name=f"{score_emoji(cat_score)} {cat.get('name', '')}",
            value=value[:1024],
            inline=False,
        )

    # Blocking issues summary
    blocking = appraisal.get("blocking_issues", [])
    if blocking:
        block_text = "\n".join(f"\U0001f6d1 {issue}" for issue in blocking)
        embed.add_field(
            name="\u274c Blocking Issues",
            value=block_text[:1024],
            inline=False,
        )

    # Recommendations
    recs = appraisal.get("recommendations", [])
    if recs:
        rec_text = "\n".join(f"\U0001f4a1 {r}" for r in recs)
        embed.add_field(
            name="\U0001f4a1 Recommendations",
            value=rec_text[:1024],
            inline=False,
        )

    platform_label = {
        "apple": "Apple App Store",
        "google": "Google Play Store",
        "both": "Apple App Store & Google Play Store",
    }.get(platform, platform)
    embed.set_footer(text=f"Evaluated against {platform_label} review guidelines")

    return embed


class AppraisalGateView(discord.ui.View):
    """Shown when appraisal finds issues before publish. Lets user proceed or fix."""

    def __init__(
        self,
        ctx: BotContext,
        channel,
        user_id: int,
        is_admin: bool,
        ws_key: str,
        ws_path: str,
        publish_target: str,
        appraisal: dict,
    ):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.publish_target = publish_target  # "testflight" or "playstore"
        self.appraisal = appraisal

    @discord.ui.button(label="Publish anyway", style=discord.ButtonStyle.danger, emoji="\U0001f680")
    async def publish_anyway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)

        # Disable buttons and acknowledge interaction in one call
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        await self.channel.send(f"\U0001f680 Proceeding to {self.publish_target}...")

        # Late imports to avoid circular deps
        from handlers.publish_commands import (
            _run_testflight_publish,
            _run_playstore_publish,
        )

        if self.publish_target == "testflight":
            await _run_testflight_publish(
                self.ctx, self.channel, self.user_id, self.ws_key, self.ws_path,
            )
        else:
            await _run_playstore_publish(
                self.ctx, self.channel, self.user_id, self.ws_key, self.ws_path,
                self.is_admin,
            )

    @discord.ui.button(label="Fix issues first", style=discord.ButtonStyle.secondary, emoji="\U0001f527")
    async def fix_issues(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)

        # Build fix suggestions — skip auto-fixed items, only show actionable issues
        lines = []
        for cat in self.appraisal.get("categories", []):
            if cat.get("name") == "Auto-Fixed":
                continue
            for f in cat.get("findings", []):
                hint = f.get("fix_hint", "")
                if hint and f.get("severity") in ("critical", "warning"):
                    sev = severity_emoji(f.get("severity", "info"))
                    lines.append(f"{sev} **{f.get('title', '')}**\n\u2192 {hint}")

        if not lines:
            lines.append("No specific fix hints available.")

        fix_text = "\n\n".join(lines)[:1900]
        fix_text += "\n\nTip: send `@workspace fix the appraisal issues` to auto-fix."

        await interaction.response.send_message(fix_text, ephemeral=False)
