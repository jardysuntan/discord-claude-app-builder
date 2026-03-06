"""
handlers/system_commands.py — System and utility commands
(spend, memory, fixes, setup, health, reload, patch-bot, bot-todo,
 dashboard, newsession, maintenance, announce, queue, unknown).

Extracted from bot.py lines 2256-2731.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import config
from commands import memory_cmd, fixes_cmd, queue
from commands.bot_todo import handle_bot_todo
from commands.dashboard import handle_dashboard
from helpers.ui_helpers import send_workspace_footer
from views.queue_views import QueueBuilderView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


async def handle_spend(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    user_cap = ctx.allowlist.get_daily_cap(user_id)
    my_spent = ctx.cost_tracker.today_spent(user_id)
    my_tasks = ctx.cost_tracker.today_tasks(user_id)
    my_remaining = max(0, user_cap - my_spent)
    lines = [
        "💰 **Your Daily Spend**",
        f"  Today: ${my_spent:.4f}",
        f"  Budget: ${user_cap:.2f}",
        f"  Remaining: ${my_remaining:.2f}",
        f"  Tasks: {my_tasks}",
    ]
    if is_admin:
        global_spent = ctx.cost_tracker.today_spent()
        global_tasks = ctx.cost_tracker.today_tasks()
        lines.append("\n📊 **Global**")
        lines.append(f"  Total: ${global_spent:.4f} ({global_tasks} tasks)")
        for uid, spent, tasks in ctx.cost_tracker.user_summaries():
            name = ctx.allowlist.get_display_name(uid) or str(uid)
            lines.append(f"  {name}: ${spent:.4f} ({tasks} tasks)")
    await ctx.send(channel, "\n".join(lines))


async def handle_memory(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, memory_cmd.handle_memory(
            cmd.sub, cmd.arg, ws_path, ws_key))


async def handle_fixes(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_path:
        await ctx.send(channel, "❌ No workspace set.")
    else:
        await ctx.send(channel, fixes_cmd.handle_fixes(
            cmd.sub, ws_path, ws_key))


async def handle_setup(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    import shutil
    checks = []

    # Claude
    claude_path = shutil.which(config.CLAUDE_BIN)
    checks.append(
        f"{'✅' if claude_path else '❌'} **Claude CLI** — `{claude_path or 'not found'}`"
    )

    # Android
    adb_path = shutil.which(config.ADB_BIN)
    has_avd = bool(config.ANDROID_AVD)
    checks.append(
        f"{'✅' if adb_path else '❌'} **Android SDK** — adb: `{adb_path or 'not found'}`"
    )
    checks.append(
        f"{'✅' if has_avd else '⚠️'} **Android AVD** — "
        f"`{config.ANDROID_AVD or 'not set (set ANDROID_AVD in .env)'}`"
    )

    # iOS
    xcode_path = shutil.which(config.XCODEBUILD)
    checks.append(
        f"{'✅' if xcode_path else '❌'} **Xcode** — "
        f"`{xcode_path or 'not found (install from App Store)'}`"
    )
    checks.append(f"  Simulator: `{config.IOS_SIMULATOR_NAME}`")

    # TestFlight
    has_tf = bool(config.APPLE_TEAM_ID and config.ASC_KEY_ID and config.ASC_ISSUER_ID)
    if has_tf:
        checks.append(f"✅ **TestFlight** — Team: `{config.APPLE_TEAM_ID}`")
    else:
        missing_tf = []
        if not config.APPLE_TEAM_ID:
            missing_tf.append("APPLE_TEAM_ID")
        if not config.ASC_KEY_ID:
            missing_tf.append("ASC_KEY_ID")
        if not config.ASC_ISSUER_ID:
            missing_tf.append("ASC_ISSUER_ID")
        checks.append(f"❌ **TestFlight** — missing: `{', '.join(missing_tf)}`")

    # Play Store
    has_ps = bool(config.PLAY_JSON_KEY_PATH)
    if has_ps:
        checks.append(
            f"✅ **Play Store** — key: `{Path(config.PLAY_JSON_KEY_PATH).name}`"
        )
    else:
        checks.append("❌ **Play Store** — missing: `PLAY_JSON_KEY_PATH`")

    # Web
    checks.append(f"✅ **Web** — port `{config.WEB_SERVE_PORT}`")

    # Tailscale
    if config.TAILSCALE_HOSTNAME:
        checks.append(f"✅ **Tailscale** — `{config.TAILSCALE_HOSTNAME}`")
    else:
        checks.append("⚠️ **Tailscale** — not set (optional, for remote access)")

    # Agent mode
    checks.append(
        f"{'✅' if config.AGENT_MODE else '❌'} **Agent mode** — "
        f"{'ON' if config.AGENT_MODE else 'OFF (set AGENT_MODE=1 in .env)'}"
    )

    await ctx.send(channel, "**Setup Status**\n\n" + "\n".join(checks))


async def handle_health(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    uptime = int(time.time() - ctx.start_time)
    m, s = divmod(uptime, 60)
    h, m = divmod(m, 60)
    ws = ctx.registry.get_default(user_id) or "(none)"
    sess = ctx.claude.get_session(ws) or "(none)"
    await ctx.send(channel, (
        f"**Health**\n"
        f"  Uptime: {h}h {m}m {s}s\n"
        f"  Workspace: {ws}\n"
        f"  Session: `{sess[:20]}`\n"
        f"  Agent: {'ON' if config.AGENT_MODE else 'OFF'}\n"
        f"  Workspaces: {len(ctx.registry.list_keys())}\n"
        f"  Platforms: Android \u00b7 iOS \u00b7 Web"
    ))


async def handle_reload(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    await ctx.send(channel, "♻️ Restarting via pm2…")
    os.system("pm2 restart discord-claude-bridge")


async def handle_patch_bot(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    pass  # retired


async def handle_bot_todo_cmd(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    await ctx.send(channel, handle_bot_todo(cmd.raw_cmd))


async def handle_dashboard_cmd(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        async def dash_status(msg, fpath=None):
            await ctx.send(channel, msg, file_path=fpath)

        await handle_dashboard(
            ctx.registry, dash_status, rebuild=(cmd.sub == "rebuild"),
        )


async def handle_newsession(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    ws_key = ctx.registry.get_default(user_id)
    if ws_key:
        ctx.claude.clear_session(ws_key)
        await ctx.send(channel, f"🔄 Fresh session for **{ws_key}**.")
    else:
        await ctx.send(channel, "❌ No workspace set.")


async def handle_maintenance(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if cmd.raw_cmd and cmd.raw_cmd.lower() == "off":
        ctx.maintenance_mode = False
        await ctx.send(channel, "✅ Maintenance mode **OFF** — public commands are live.")
    else:
        ctx.maintenance_mode = True
        if cmd.raw_cmd:
            ctx.maintenance_message = f"🔧 {cmd.raw_cmd}"
        else:
            ctx.maintenance_message = "🔧 Bot is under maintenance — back shortly!"
        await ctx.send(
            channel,
            f"🔧 Maintenance mode **ON**\nPublic users see: *{ctx.maintenance_message}*",
        )


async def handle_announce(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not is_admin:
        await ctx.send(channel, "🔒 Admin-only command.")
        await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)
        return
    if not cmd.raw_cmd:
        await ctx.send(channel, "Usage: `/announce <message>`")
    else:
        # Send to announce channel if configured, otherwise just echo in current DM
        target = None
        if config.DISCORD_ANNOUNCE_CHANNEL_ID:
            target = ctx.client.get_channel(config.DISCORD_ANNOUNCE_CHANNEL_ID)
        if target:
            await target.send(f"📢 {cmd.raw_cmd}")
            await ctx.send(channel, f"✅ Announced in #{target.name}")
        else:
            await ctx.send(channel, f"📢 {cmd.raw_cmd}")


async def handle_queue_cmd(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    if not config.AGENT_MODE:
        await ctx.send(channel, "🔒 Agent mode OFF.")
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        if not ws_path:
            await ctx.send(channel, "❌ No workspace set.")
        elif not cmd.raw_cmd:
            # Interactive wizard
            view = QueueBuilderView(ctx, user_id, channel, ws_key, ws_path)
            await channel.send(view.build_message(), view=view)
        else:
            # Inline syntax: /queue task1 --- task2
            async def queue_status(msg, fpath=None):
                await ctx.send(channel, msg, file_path=fpath)

            await queue.handle_queue(
                cmd.raw_cmd, ws_key, ws_path, ctx.claude, ctx.cost_tracker,
                on_status=queue_status, user_id=user_id,
            )


async def handle_unknown(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    await ctx.send(channel, "❓ Unknown command. `/help`")


HANDLERS = {
    "spend": handle_spend,
    "memory": handle_memory,
    "fixes": handle_fixes,
    "setup": handle_setup,
    "health": handle_health,
    "reload": handle_reload,
    "patch-bot": handle_patch_bot,
    "bot-todo": handle_bot_todo_cmd,
    "dashboard": handle_dashboard_cmd,
    "newsession": handle_newsession,
    "maintenance": handle_maintenance,
    "announce": handle_announce,
    "queue": handle_queue_cmd,
    "unknown": handle_unknown,
}
