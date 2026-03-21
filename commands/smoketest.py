"""
commands/smoketest.py — /smoketest slash command + standalone script.

Runs a full buildapp → demo cycle with a deterministic prompt,
validates every stage, records results, and reports back.

Usage as slash command:
    /smoketest

Usage as standalone script:
    python -m commands.smoketest [--channel CHANNEL_ID]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import config
from helpers.smoketest_runner import run_smoketest

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_smoketest(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool,
) -> None:
    """Handler for the /smoketest slash command."""
    if not is_admin:
        await ctx.send(channel, "Admin only.")
        return

    async def on_status(msg: str, file_path: Optional[str] = None):
        await ctx.send(channel, msg, file_path=file_path)

    result = await run_smoketest(
        registry=ctx.registry,
        claude=ctx.claude,
        on_status=on_status,
        is_admin=is_admin,
        owner_id=user_id,
    )

    # Attempt auto-fix if smoke test failed
    pr_url = None
    if not result.success and config.AUTO_FIX_ON_FAILURE:
        await ctx.send(channel, "🔧 Smoke test failed — attempting auto-fix...")
        try:
            from helpers.autofix import attempt_autofix
            pr_url = attempt_autofix(result)
            if pr_url:
                await ctx.send(channel, f"✅ Auto-fix PR created: {pr_url}")
            else:
                await ctx.send(channel, "⚠️ Auto-fix could not produce a fix.")
        except Exception as exc:
            await ctx.send(channel, f"⚠️ Auto-fix error: {exc}")

    summary = result.summary()
    if pr_url:
        summary += f"\n\n🔧 **Auto-fix PR:** {pr_url}"
    await ctx.send(channel, summary)

    # Cross-post failures to reliability channel
    if not result.success and config.SMOKETEST_CHANNEL_ID:
        try:
            import discord
            reliability_ch = ctx.bot.get_channel(config.SMOKETEST_CHANNEL_ID)
            if reliability_ch:
                await reliability_ch.send(summary)
        except Exception:
            pass


# ── Standalone entry-point ───────────────────────────────────────────────────

async def _run_standalone(channel_id: Optional[int] = None) -> None:
    """Run the smoke test outside of Discord, printing results to stdout.

    If *channel_id* is provided and a Discord client is available, results
    are also posted to that channel.
    """
    import config as _cfg
    from workspaces import WorkspaceRegistry
    from claude_runner import ClaudeRunner

    registry = WorkspaceRegistry()
    claude = ClaudeRunner()

    collected: list[str] = []

    async def on_status(msg: str, file_path: Optional[str] = None):
        cleaned = msg.replace("**", "").replace("`", "")
        print(cleaned)
        collected.append(msg)

    result = await run_smoketest(
        registry=registry,
        claude=claude,
        on_status=on_status,
        is_admin=False,
        owner_id=_cfg.DISCORD_ALLOWED_USER_ID or None,
    )

    # Attempt auto-fix if smoke test failed
    pr_url = None
    if not result.success and _cfg.AUTO_FIX_ON_FAILURE:
        print("\n🔧 Smoke test failed — attempting auto-fix...")
        try:
            from helpers.autofix import attempt_autofix
            pr_url = attempt_autofix(result)
            if pr_url:
                print(f"✅ Auto-fix PR created: {pr_url}")
            else:
                print("⚠️ Auto-fix could not produce a fix.")
        except Exception as exc:
            print(f"⚠️ Auto-fix error: {exc}")

    summary = result.summary()
    if pr_url:
        summary += f"\n\n🔧 **Auto-fix PR:** {pr_url}"

    print("\n" + "=" * 60)
    print(summary.replace("**", "").replace("`", ""))
    print("=" * 60)

    # Post to Discord channel if requested
    if channel_id and _cfg.DISCORD_BOT_TOKEN:
        import discord

        intents = discord.Intents.default()
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            ch = client.get_channel(channel_id)
            if ch:
                await ch.send(summary)
            await client.close()

        await client.start(_cfg.DISCORD_BOT_TOKEN)

    return result


def main():
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Run smoke test")
    parser.add_argument(
        "--channel", type=int, default=None,
        help="Discord channel ID to post results to (defaults to SMOKETEST_CHANNEL_ID)",
    )
    args = parser.parse_args()
    channel_id = args.channel or config.SMOKETEST_CHANNEL_ID or None
    asyncio.run(_run_standalone(channel_id))


if __name__ == "__main__":
    main()
