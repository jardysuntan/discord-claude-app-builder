"""
bot_context.py — shared context object for the Discord bot.

Holds all the globals (client, registry, runner, tracker, allowlist)
plus ephemeral per-session state so command modules can access them
without circular imports back into bot.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import discord

import config
from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner
from cost_tracker import CostTracker
from allowlist import Allowlist

# ── Constants ────────────────────────────────────────────────────────────────

STILL_LISTENING = "\U0001f4a1 *I'm still listening \u2014 feel free to send other commands while this runs.*"


# ── Context dataclass ────────────────────────────────────────────────────────

@dataclass
class BotContext:
    """Single object that bundles every piece of shared bot state."""

    client: discord.Client
    registry: WorkspaceRegistry
    claude: ClaudeRunner
    cost_tracker: CostTracker
    allowlist: Allowlist

    # Runtime toggles
    maintenance_mode: bool = False
    maintenance_message: str = "\U0001f527 Bot is under maintenance \u2014 back shortly!"

    # Ephemeral per-session state
    interview_pending: set = field(default_factory=set)          # set of (channel_id, user_id)
    awaiting_json_upload: dict = field(default_factory=dict)     # user_id -> (ws_key, ws_path, checklist_msg)

    start_time: float = 0.0

    # ── Helpers ──────────────────────────────────────────────────────────

    async def send(self, channel, text: str, file_path: str | None = None):
        """Send a message to *channel*, truncating if needed.

        Optionally attaches a file when *file_path* points to an existing path.
        """
        if len(text) > config.MAX_DISCORD_MSG_LEN:
            text = text[: config.MAX_DISCORD_MSG_LEN] + "\n\u2026(truncated)"
        kwargs: dict = {"content": text}
        if file_path and Path(file_path).exists():
            kwargs["file"] = discord.File(file_path)
        await channel.send(**kwargs)
