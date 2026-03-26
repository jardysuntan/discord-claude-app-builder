"""
bot.py — discord-claude-bridge v2 (Kotlin Multiplatform edition)
Entry point. Discord client, message routing, response dispatch.
"""

import asyncio
import sys
import time
from pathlib import Path

import discord

import config
import parser as msg_parser
from parser import WorkspacePrompt, Command, FallbackPrompt
from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner
from cost_tracker import CostTracker
from allowlist import Allowlist
from bot_context import BotContext
from handlers import COMMAND_HANDLERS
from handlers.prompt_handler import handle_prompt
from handlers.public_commands import HANDLERS as PUBLIC_HANDLERS
from helpers.ui_helpers import send_workspace_footer
from helpers.welcome import welcome_embed, WelcomeView
from commands.playstore_state import PlayStoreState
from views.playstore_views import PlayStoreChecklistView, _playstore_checklist_embed

# ── Startup ──────────────────────────────────────────────────────────────────

problems = config.validate()
if problems:
    for p in problems:
        print(f"  ❌ {p}")
    sys.exit(1)

print("🤖 discord-claude-bridge v2 (KMP)")
config.print_config_summary()

registry = WorkspaceRegistry()
claude = ClaudeRunner()
cost_tracker = CostTracker()
allowlist = Allowlist()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed for auto-approve (guild.get_member)
client = discord.Client(intents=intents)

ctx = BotContext(
    client=client,
    registry=registry,
    claude=claude,
    cost_tracker=cost_tracker,
    allowlist=allowlist,
    start_time=time.time(),
)


# ── Events ───────────────────────────────────────────────────────────────────

_startup_announced = False


@client.event
async def on_ready():
    global _startup_announced
    print(f"✅ Logged in as {client.user}")
    if _startup_announced:
        print("  (on_ready fired again — skipping announcement)")
        return
    _startup_announced = True
    await asyncio.sleep(3)
    try:
        owner = await client.fetch_user(config.DISCORD_ALLOWED_USER_ID)
        if owner:
            ws = registry.get_default(config.DISCORD_ALLOWED_USER_ID)
            ws_line = f"\n📂 workspace: **{ws}**" if ws else ""
            await owner.send(f"✅ Bot is back online and updated!{ws_line}")
            print(f"  Announced to {owner.display_name}")
    except Exception as e:
        print(f"  ⚠️ Could not DM owner: {e}")


@client.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    try:
        dm = await member.create_dm()
        await dm.send(embed=welcome_embed(), view=WelcomeView())
    except Exception as e:
        print(f"  ⚠️ Could not DM new member {member}: {e}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ── Play Store JSON key upload ────────────────────────────────────
    uid = message.author.id
    if uid in ctx.awaiting_json_upload and message.attachments:
        att = next((a for a in message.attachments if a.filename.endswith(".json")), None)
        if att:
            ws_key, ws_path, checklist_msg = ctx.awaiting_json_upload.pop(uid)
            dest = Path(ws_path) / "play-service-account.json"
            await att.save(dest)
            state = PlayStoreState.load(ws_path)
            state.json_key_path = str(dest)
            state.save(ws_path)
            from platforms import AndroidPlatform
            pkg = AndroidPlatform.parse_app_id(ws_path) or ""
            app_name = ws_key.replace("-", " ").replace("_", " ").title()
            new_view = PlayStoreChecklistView(ctx, uid, ws_key, ws_path, app_name, pkg)
            try:
                await checklist_msg.edit(
                    embed=_playstore_checklist_embed(ws_key, app_name, pkg, new_view.state),
                    view=new_view,
                )
            except Exception:
                pass
            await ctx.send(message.channel, f"✅ Service account key saved for **{ws_key}**.")
            return

    # ── CSV data import upload ────────────────────────────────────
    if uid in ctx.awaiting_csv_upload and message.attachments:
        att = next((a for a in message.attachments if a.filename.lower().endswith(".csv")), None)
        if att:
            ws_key, ws_path = ctx.awaiting_csv_upload.pop(uid)
            from handlers.data_commands import _process_csv_import
            await _process_csv_import(ctx, message.channel, ws_path, att)
            return

    text = message.content.strip()
    has_images = any(
        att.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))
        for att in message.attachments
    )
    if not text and not has_images:
        return

    # Image-only message: treat as a visual bug report
    if not text and has_images:
        text = "Fix the visual bug shown in the attached screenshot(s). Compare against the current app state and make the necessary code changes."

    parsed = msg_parser.parse(text)
    channel = message.channel
    is_dm = isinstance(channel, discord.DMChannel)
    user_id = message.author.id
    is_admin = allowlist.is_admin(user_id)
    is_allowed = allowlist.is_allowed(user_id)

    # ── Public commands (server + DM, any user) ──────────────────────────
    if isinstance(parsed, Command) and parsed.name in PUBLIC_HANDLERS:
        if ctx.maintenance_mode and not is_admin:
            return await ctx.send(channel, ctx.maintenance_message)
        if not config.AGENT_MODE:
            return await ctx.send(channel, "🔒 Agent mode is OFF.")
        handler = PUBLIC_HANDLERS[parsed.name]
        await handler(ctx, parsed, channel, user_id, is_admin)
        return

    # ── Everything below: DM-only, allowed users ────────────────────────
    if not is_dm:
        return

    # Auto-approve: if user shares a guild with the bot, add them automatically
    if not is_allowed:
        shares_guild = bool(message.author.mutual_guilds) or any(
            guild.get_member(user_id) for guild in client.guilds
        )
        if shares_guild:
            display = message.author.display_name
            allowlist.add(user_id, display)
            is_allowed = True
        else:
            return

    # ── Claude prompts ───────────────────────────────────────────────────
    if isinstance(parsed, (WorkspacePrompt, FallbackPrompt)):
        await handle_prompt(ctx, parsed, channel, user_id, is_admin, attachments=message.attachments)
        return

    # ── Commands ─────────────────────────────────────────────────────────
    if not isinstance(parsed, Command):
        return

    cmd = parsed
    handler = COMMAND_HANDLERS.get(cmd.name)
    if handler:
        await handler(ctx, cmd, channel, user_id, is_admin)
    else:
        await ctx.send(channel, "❓ Unknown command. `/help`")

    # Workspace footer — always show after every command
    selector_view = getattr(cmd, '_selector_view', None)
    await send_workspace_footer(ctx, channel, user_id, selector_view=selector_view, is_admin=is_admin)


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client.run(config.DISCORD_BOT_TOKEN)
