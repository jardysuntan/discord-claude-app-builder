"""
views/security_views.py — Security scan report embed and publish gate view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_context import BotContext
    from commands.security_scan import SecurityScanResult


# ── Severity helpers ───────────────────────────────────────────────────────

_SEVERITY_EMOJI = {
    "critical": "\U0001f6d1",  # 🛑
    "warning": "\u26a0\ufe0f",  # ⚠️
    "info": "\U0001f4a1",      # 💡
}

_RESULT_COLOR = {
    True: 0xFF3B30,   # red — has critical / blocked
    False: 0xFF9500,  # orange — warnings only
}


def _sev_emoji(severity: str) -> str:
    return _SEVERITY_EMOJI.get(severity, "\u2022")


# ── Embed builder ──────────────────────────────────────────────────────────

def security_scan_embed(scan: SecurityScanResult) -> discord.Embed:
    """Build a Discord embed showing security scan results."""
    blocked = scan.should_block

    if blocked:
        title = "\U0001f6a8 Security Scan: BLOCKED"
        description = (
            "Critical security issues found — publish blocked.\n"
            "Fix the issues below or use `--skip-security` to override."
        )
    elif scan.findings or scan.claude_findings:
        title = "\u26a0\ufe0f Security Scan: WARNINGS"
        description = "Non-critical security issues found. Review recommended."
    else:
        title = "\u2705 Security Scan: PASSED"
        description = "No security issues detected."

    embed = discord.Embed(
        title=title,
        description=description,
        color=_RESULT_COLOR.get(blocked, 0x34C759),
    )

    # Deterministic findings
    if scan.findings:
        critical = [f for f in scan.findings if f.severity == "critical"]
        warnings = [f for f in scan.findings if f.severity == "warning"]
        infos = [f for f in scan.findings if f.severity == "info"]

        for group, label in [
            (critical, "\U0001f6d1 Critical Issues"),
            (warnings, "\u26a0\ufe0f Warnings"),
            (infos, "\U0001f4a1 Info"),
        ]:
            if not group:
                continue
            lines = []
            for f in group:
                entry = f"{_sev_emoji(f.severity)} **{f.title}**\n{f.detail}"
                if f.fix_hint:
                    entry += f"\n\u2192 *{f.fix_hint}*"
                lines.append(entry)
            embed.add_field(
                name=label,
                value="\n\n".join(lines)[:1024],
                inline=False,
            )

    # Claude self-audit findings
    if scan.claude_findings:
        lines = []
        for cf in scan.claude_findings:
            sev = _sev_emoji(cf.get("severity", "info"))
            title_text = cf.get("title", "")
            detail = cf.get("detail", "")
            hint = cf.get("fix_hint", "")
            entry = f"{sev} **{title_text}**\n{detail}"
            if hint:
                entry += f"\n\u2192 *{hint}*"
            lines.append(entry)
        embed.add_field(
            name="\U0001f916 AI Security Audit",
            value="\n\n".join(lines)[:1024],
            inline=False,
        )

    embed.set_footer(
        text=f"Scan completed in {scan.scan_time_s:.1f}s \u2022 "
             "Pre-publish security gate",
    )

    return embed


# ── Gate view (shown when scan blocks publish) ─────────────────────────────

class SecurityGateView(discord.ui.View):
    """Shown when security scan finds critical issues. Lets user override or fix."""

    def __init__(
        self,
        ctx: BotContext,
        channel,
        user_id: int,
        is_admin: bool,
        ws_key: str,
        ws_path: str,
        publish_target: str,
        scan: SecurityScanResult,
    ):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.channel = channel
        self.user_id = user_id
        self.is_admin = is_admin
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.publish_target = publish_target
        self.scan = scan

    @discord.ui.button(
        label="Publish anyway (skip security)",
        style=discord.ButtonStyle.danger,
        emoji="\u26a0\ufe0f",
    )
    async def skip_security(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        await self.channel.send(
            "\u26a0\ufe0f **Security override** — publishing with known security issues. "
            "This has been logged for audit trail."
        )

        import logging
        log = logging.getLogger("commands.security_scan")
        log.warning(
            "security_scan OVERRIDE: user=%d ws=%s target=%s critical_count=%d",
            self.user_id, self.ws_key, self.publish_target,
            sum(1 for f in self.scan.findings if f.severity == "critical"),
        )

        from handlers.publish_commands import (
            _run_testflight_publish,
            _run_playstore_publish,
        )

        if self.publish_target == "testflight":
            await _run_testflight_publish(
                self.ctx, self.channel, self.user_id,
                self.ws_key, self.ws_path,
            )
        else:
            await _run_playstore_publish(
                self.ctx, self.channel, self.user_id,
                self.ws_key, self.ws_path, self.is_admin,
            )

    @discord.ui.button(
        label="Fix issues first",
        style=discord.ButtonStyle.secondary,
        emoji="\U0001f527",
    )
    async def fix_issues(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your command.", ephemeral=True,
            )

        lines = []
        for f in self.scan.findings:
            if f.fix_hint and f.severity in ("critical", "warning"):
                lines.append(
                    f"{_sev_emoji(f.severity)} **{f.title}**\n\u2192 {f.fix_hint}"
                )
        for cf in self.scan.claude_findings:
            hint = cf.get("fix_hint", "")
            if hint and cf.get("severity") in ("critical", "warning"):
                sev = _sev_emoji(cf.get("severity", "info"))
                lines.append(f"{sev} **{cf.get('title', '')}**\n\u2192 {hint}")

        if not lines:
            lines.append("No specific fix hints available.")

        fix_text = "\n\n".join(lines)[:1900]
        fix_text += "\n\nTip: send `fix the security issues` to auto-fix."

        await interaction.response.send_message(fix_text, ephemeral=False)
