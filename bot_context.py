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


def _split_message(text: str, limit: int) -> list[str]:
    """Split *text* into chunks of at most *limit* chars, breaking at newlines."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Find last newline within the limit
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            # No newline found — hard-split at limit
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


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
    awaiting_csv_upload: dict = field(default_factory=dict)      # user_id -> (ws_key, ws_path)

    start_time: float = 0.0

    # ── Helpers ──────────────────────────────────────────────────────────

    async def send(self, channel, text: str, file_path: str | None = None):
        """Send a message to *channel*, splitting into chunks if needed.

        - Messages ≤ 1900 chars: sent as-is.
        - Messages > 1900 chars: split at newline boundaries into multiple messages.
        - Messages > 12000 chars: first chunk sent as text, full response attached as .txt file.
        Optionally attaches a file when *file_path* points to an existing path.
        """
        limit = config.MAX_DISCORD_MSG_LEN
        attach_file = file_path and Path(file_path).exists()

        if len(text) <= limit:
            kwargs: dict = {"content": text}
            if attach_file:
                kwargs["file"] = discord.File(file_path)
            await channel.send(**kwargs)
            return

        # Very long output: attach as file
        txt_file = None
        if len(text) > 12000:
            import io
            txt_file = discord.File(
                io.BytesIO(text.encode()), filename="full_response.txt"
            )

        # Split into chunks at newline boundaries
        chunks = _split_message(text, limit)
        for i, chunk in enumerate(chunks):
            kwargs = {"content": chunk}
            is_last = i == len(chunks) - 1
            if is_last and txt_file:
                kwargs["file"] = txt_file
            elif is_last and attach_file:
                kwargs["file"] = discord.File(file_path)
            await channel.send(**kwargs)
