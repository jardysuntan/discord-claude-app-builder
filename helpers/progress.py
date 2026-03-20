"""
helpers/progress.py — Edit-in-place progress message for Discord.

Instead of flooding the channel with 30+ messages, sends ONE message
and edits it with rolling status updates.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# How many recent status lines to keep visible
_MAX_HISTORY = 5


def _elapsed(seconds: float) -> str:
    """Format elapsed seconds as a compact duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}m{secs:02d}s"


class ProgressMessage:
    """Manages a single Discord message that gets edited with progress updates.

    Usage::

        progress = ProgressMessage(ctx, channel, title="Building iOS")
        await progress.update("Booting simulator...")
        await progress.update("Compiling...")
        await progress.close()  # optional: remove spinner feel
    """

    def __init__(self, ctx, channel, *, title: str = ""):
        self._ctx = ctx
        self._channel = channel
        self._title = title
        self._message = None          # discord.Message once sent
        self._history: list[str] = []
        self._closed = False
        self._start_time = time.monotonic()

    def _render(self) -> str:
        """Build the message content from title + rolling history."""
        lines: list[str] = []
        if self._title:
            lines.append(f"**{self._title}**")
        for entry in self._history:
            lines.append(entry)
        return "\n".join(lines) or "⏳ Starting..."

    async def update(self, text: str) -> None:
        """Add a status line and edit (or send) the progress message."""
        if self._closed:
            # After close, fall back to new messages
            await self._ctx.send(self._channel, text)
            return

        elapsed = _elapsed(time.monotonic() - self._start_time)
        self._history.append(f"`[{elapsed}]` {text}")
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        content = self._render()

        # Discord message limit
        if len(content) > 1900:
            self._history = self._history[-3:]
            content = self._render()

        if self._message is None:
            # First update — send the initial message
            self._message = await self._channel.send(content)
        else:
            try:
                await self._message.edit(content=content)
            except Exception:
                # Edit failed (message too old, deleted, etc.) — start a new one
                log.debug("Progress edit failed, sending new message", exc_info=True)
                self._message = await self._channel.send(content)

    async def close(self) -> None:
        """Mark progress as done. Further updates fall back to new messages."""
        self._closed = True

    async def status_callback(self, text: str) -> None:
        """Drop-in replacement for the ``on_status`` callback in agent_loop."""
        await self.update(text)
