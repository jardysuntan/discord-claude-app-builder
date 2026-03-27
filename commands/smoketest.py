"""
commands/smoketest.py — /smoketest slash command + standalone script.

Runs a full buildapp → demo cycle with deterministic prompts,
validates every stage, records results, and reports back.

Usage as slash command:
    /smoketest
    /smoketest --scenario counter
    /smoketest --api

Usage as standalone script:
    python -m commands.smoketest [--channel CHANNEL_ID]
    python -m commands.smoketest --scenario counter
    python -m commands.smoketest --scenario map
    python -m commands.smoketest --api
    python -m commands.smoketest --scenario all --api
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import config
from helpers.smoketest_runner import run_smoketest, SCENARIO_NAMES

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

    # Parse scenario filter from command args
    scenarios = _parse_scenario_arg(cmd.args if hasattr(cmd, "args") else "")
    run_api = "--api" in (cmd.args if hasattr(cmd, "args") else "")

    result = await run_smoketest(
        registry=ctx.registry,
        claude=ctx.claude,
        on_status=on_status,
        is_admin=is_admin,
        owner_id=user_id,
        scenarios=scenarios,
    )

    # Attempt auto-fix if smoke test failed
    pr_url = None
    if not result.success and config.AUTO_FIX_ON_FAILURE:
        await ctx.send(channel, "\U0001f527 Smoke test failed — attempting auto-fix...")
        try:
            from helpers.autofix import attempt_autofix
            pr_url = attempt_autofix(result)
            if pr_url:
                await ctx.send(channel, f"\u2705 Auto-fix PR created: {pr_url}")
            else:
                await ctx.send(channel, "\u26a0\ufe0f Auto-fix could not produce a fix.")
        except Exception as exc:
            await ctx.send(channel, f"\u26a0\ufe0f Auto-fix error: {exc}")

    summary = result.summary()
    if pr_url:
        summary += f"\n\n\U0001f527 **Auto-fix PR:** {pr_url}"
    await ctx.send(channel, summary)

    # Run API smoke tests if requested
    if run_api:
        await ctx.send(channel, "\U0001f310 Running API smoke tests...")
        api_summary = await _run_api_tests()
        await ctx.send(channel, api_summary)

    # Cross-post failures to reliability channel
    if not result.success and config.SMOKETEST_CHANNEL_ID:
        try:
            import discord
            reliability_ch = ctx.bot.get_channel(config.SMOKETEST_CHANNEL_ID)
            if reliability_ch:
                await reliability_ch.send(summary)
        except Exception:
            pass


def _parse_scenario_arg(args: str) -> list[str] | None:
    """Extract --scenario value from args string. Returns None for 'all' or unspecified."""
    if not args:
        return None
    parts = args.split()
    for i, part in enumerate(parts):
        if part == "--scenario" and i + 1 < len(parts):
            value = parts[i + 1].lower()
            if value == "all":
                return None
            if value in SCENARIO_NAMES:
                return [value]
    return None


async def _run_api_tests() -> str:
    """Run API smoke tests and return summary string."""
    import os
    from pathlib import Path
    from helpers.api_smoketest import run_api_smoketest

    port = os.getenv("API_PORT", "8100")
    base_url = f"http://localhost:{port}"

    # Read token from .api-token file or env
    token = os.getenv("API_TOKEN")
    if not token:
        token_file = Path(__file__).parent.parent / ".api-token"
        if token_file.exists():
            token = token_file.read_text().strip()

    result = await run_api_smoketest(base_url=base_url, token=token)
    return result.summary()


# ── Standalone entry-point ───────────────────────────────────────────────────

async def _run_standalone(
    channel_id: Optional[int] = None,
    scenarios: list[str] | None = None,
    run_api: bool = False,
    run_builds: bool = True,
) -> None:
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

    # Run build scenarios (unless --api-only)
    result = None
    if run_builds:
        # Run scenarios if explicitly specified, or if --api wasn't the only flag
        result = await run_smoketest(
            registry=registry,
            claude=claude,
            on_status=on_status,
            is_admin=False,
            owner_id=_cfg.DISCORD_ALLOWED_USER_ID or None,
            scenarios=scenarios,
        )

        # Attempt auto-fix if smoke test failed
        pr_url = None
        if not result.success and _cfg.AUTO_FIX_ON_FAILURE:
            print("\n\U0001f527 Smoke test failed — attempting auto-fix...")
            try:
                from helpers.autofix import attempt_autofix
                pr_url = attempt_autofix(result)
                if pr_url:
                    print(f"\u2705 Auto-fix PR created: {pr_url}")
                else:
                    print("\u26a0\ufe0f Auto-fix could not produce a fix.")
            except Exception as exc:
                print(f"\u26a0\ufe0f Auto-fix error: {exc}")

        summary = result.summary()
        if pr_url:
            summary += f"\n\n\U0001f527 **Auto-fix PR:** {pr_url}"

        print("\n" + "=" * 60)
        print(summary.replace("**", "").replace("`", ""))
        print("=" * 60)

    # Run API smoke tests
    api_summary = None
    if run_api:
        print("\n\U0001f310 Running API smoke tests...")
        api_summary = await _run_api_tests()
        print("\n" + api_summary.replace("**", "").replace("`", ""))

    # Post to Discord channel if requested
    combined_summary = ""
    if result:
        combined_summary += result.summary()
    if api_summary:
        combined_summary += "\n\n" + api_summary

    if channel_id and _cfg.DISCORD_BOT_TOKEN and combined_summary:
        import discord

        intents = discord.Intents.default()
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            ch = client.get_channel(channel_id)
            if ch:
                await ch.send(combined_summary)
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
    parser.add_argument(
        "--scenario", type=str, default=None,
        choices=SCENARIO_NAMES + ["all"],
        help="Which scenario to run (default: all)",
    )
    parser.add_argument(
        "--api", action="store_true",
        help="Run API endpoint smoke tests",
    )
    args = parser.parse_args()

    channel_id = args.channel or config.SMOKETEST_CHANNEL_ID or None
    scenarios = None if args.scenario is None or args.scenario == "all" else [args.scenario]
    run_api = args.api

    # --api alone (no --scenario) → API only; otherwise run build scenarios
    run_builds = args.scenario is not None or not run_api

    asyncio.run(_run_standalone(
        channel_id,
        scenarios=scenarios,
        run_api=run_api,
        run_builds=run_builds,
    ))


if __name__ == "__main__":
    main()
