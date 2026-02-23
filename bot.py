"""
bot.py — discord-claude-bridge v2 (Kotlin Multiplatform edition)
Entry point. Discord client, message routing, response dispatch.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import discord

import config
import parser as msg_parser
from parser import WorkspacePrompt, Command, FallbackPrompt
from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner
from commands import run_cmd, memory_cmd, buildapp, fix
from commands import git_cmd
from commands.create import create_kmp_project
from platforms import demo_platform, build_platform, deploy_ios, deploy_android, AndroidPlatform, WebPlatform

# ── Startup ──────────────────────────────────────────────────────────────────

START_TIME = time.time()

problems = config.validate()
if problems:
    for p in problems:
        print(f"  ❌ {p}")
    sys.exit(1)

print("🤖 discord-claude-bridge v2 (KMP)")
config.print_config_summary()

registry = WorkspaceRegistry()
claude = ClaudeRunner()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def send(channel, text, file_path=None):
    if len(text) > config.MAX_DISCORD_MSG_LEN:
        text = text[:config.MAX_DISCORD_MSG_LEN] + "\n…(truncated)"
    kwargs = {"content": text}
    if file_path and Path(file_path).exists():
        kwargs["file"] = discord.File(file_path)
    await channel.send(**kwargs)


def help_text():
    agent = " *(agent ON)*" if config.AGENT_MODE else " *(agent OFF)*"
    return (
        "**discord-claude-bridge v2 — KMP**" + agent + "\n\n"
        "**Workspace:**\n"
        "`@<ws> <prompt>` — Claude prompt in workspace\n"
        "`/use <ws>` · `/where` · `/ls`\n\n"
        "**Build & Ship (Kotlin Multiplatform):**\n"
        "`/buildapp <description>` — idea → running app\n"
        "`/create <AppName>` — scaffold KMP project\n"
        "`/build android|ios|web` — build a target\n"
        "`/demo android|ios|web` — build + screenshot\n"
        "`/vid` — Android video recording\n"
        "`/fix [instructions]` — auto-fix build errors\n\n"
        "**Mirror & Showcase:**\n"
        "`/mirror start|stop` — emulator in your browser\n"
        "`/showcase <app>` — video demo for everyone *(server)*\n"
        "`/tryapp <app>` — live emulator for anyone *(server)*\n"
        "`/showcase gallery` · `/done`\n\n"
        "**Terminal:**\n"
        "`/run <cmd>` · `/runsh <cmd>`\n\n"
        "**Git & GitHub:**\n"
        "`/status` — branch + changed files\n"
        "`/diff` — what changed (`/diff full` for patch)\n"
        "`/commit [msg]` — commit + push (auto-generates msg)\n"
        "`/undo` — revert last commit\n"
        "`/log [n]` — recent commits\n"
        "`/branch [name]` — show or create branch\n"
        "`/stash` · `/stash pop`\n"
        "`/pr [title]` — create GitHub PR\n"
        "`/repo` · `/repo create` · `/repo set <url>`\n\n"
        "**Memory:**\n"
        "`/memory show|pin|reset`\n\n"
        "**System:**\n"
        "`/health` · `/reload` · `/newsession`\n"
        "`/patch-bot <instructions>` — Claude edits the bot itself"
    )


# ── Events ───────────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    text = message.content.strip()
    if not text:
        return

    parsed = msg_parser.parse(text)
    channel = message.channel
    is_dm = isinstance(channel, discord.DMChannel)
    is_owner = message.author.id == config.DISCORD_ALLOWED_USER_ID

    # ── Public commands (server + DM, any user) ──────────────────────────
    if isinstance(parsed, Command) and parsed.name in ("showcase", "tryapp", "gallery", "done"):
        if not config.AGENT_MODE:
            return await send(channel, "🔒 Agent mode is OFF.")

        # Lazy import to avoid circular
        from commands.showcase import handle_showcase, handle_tryapp, handle_gallery, handle_done

        async def pub_status(msg, fpath=None):
            await send(channel, msg, file_path=fpath)

        match parsed.name:
            case "showcase":
                if not parsed.workspace:
                    return await send(channel, "Usage: `/showcase <workspace>` or `/showcase gallery`")
                ws_path = registry.get_path(parsed.workspace)
                if not ws_path:
                    return await send(channel, f"❌ App `{parsed.workspace}` not found.")
                await handle_showcase(parsed.workspace, ws_path, pub_status)
            case "tryapp":
                if not parsed.workspace:
                    return await send(channel, "Usage: `/tryapp <workspace>`")
                ws_path = registry.get_path(parsed.workspace)
                if not ws_path:
                    return await send(channel, f"❌ App `{parsed.workspace}` not found.")
                await handle_tryapp(
                    parsed.workspace, ws_path,
                    message.author.id, message.author.display_name, pub_status,
                )
            case "gallery":
                await handle_gallery(pub_status)
            case "done":
                result = await handle_done(message.author.id)
                await send(channel, result)
        return

    # ── Everything below: DM-only, owner-only ────────────────────────────
    if not is_dm or not is_owner:
        return

    # ── Claude prompts ───────────────────────────────────────────────────
    if isinstance(parsed, (WorkspacePrompt, FallbackPrompt)):
        if isinstance(parsed, WorkspacePrompt):
            ws_key, ws_path = registry.resolve(parsed.workspace, message.author.id)
            prompt = parsed.prompt
        else:
            ws_key, ws_path = registry.resolve(None, message.author.id)
            prompt = parsed.prompt

        if not ws_key:
            return await send(channel, "❌ No workspace set. Use `/use <ws>` or `@ws`.")
        if not ws_path:
            return await send(channel, f"❌ Workspace `{ws_key}` not found.")

        await send(channel, f"🧠 Thinking in **{ws_key}**…")

        async def claude_progress(msg):
            await send(channel, msg)

        result = await claude.run(prompt, ws_key, ws_path, on_progress=claude_progress)
        if result.exit_code != 0 and result.stderr:
            return await send(channel, f"⚠️ Error:\n```\n{result.stderr[:1500]}\n```")
        return await send(channel, result.stdout or "(empty)")

    # ── Commands ─────────────────────────────────────────────────────────
    if not isinstance(parsed, Command):
        return

    cmd = parsed

    match cmd.name:
        case "help":
            return await send(channel, help_text())

        case "ls":
            keys = registry.list_keys()
            if keys:
                return await send(channel, "**Workspaces:**\n" + "\n".join(f"  `{k}`" for k in keys))
            return await send(channel, "No workspaces.")

        case "use":
            if not cmd.workspace:
                return await send(channel, "Usage: `/use <workspace>`")
            if registry.set_default(message.author.id, cmd.workspace):
                return await send(channel, f"✅ Default → **{cmd.workspace}**")
            return await send(channel, f"❌ Unknown: `{cmd.workspace}`")

        case "where":
            ws = registry.get_default(message.author.id)
            if ws:
                return await send(channel, f"📂 **{ws}** → `{registry.get_path(ws)}`")
            return await send(channel, "No default set.")

        case "create":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            if not cmd.app_name:
                return await send(channel, "Usage: `/create <AppName>`")
            result = await create_kmp_project(cmd.app_name, registry)
            return await send(channel, result.message)

        case "deleteapp":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            if not cmd.workspace:
                return await send(channel, "Usage: `/deleteapp <workspace>`")
            ws_key = cmd.workspace.lower()
            ws_path = registry.get_path(ws_key)
            if not ws_path:
                return await send(channel, f"❌ Unknown workspace: `{ws_key}`")
            import shutil as _shutil
            try:
                _shutil.rmtree(ws_path)
            except Exception as e:
                return await send(channel, f"❌ Failed to delete `{ws_path}`: {e}")
            registry.remove(ws_key)
            return await send(channel, f"🗑️ Deleted **{ws_key}** (`{ws_path}`)")

        case "buildapp":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            async def ba_status(msg, fpath=None):
                await send(channel, msg, file_path=fpath)
            await buildapp.handle_buildapp(cmd.raw_cmd or "", registry, claude, ba_status)

        case "build":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            platform = cmd.platform or "android"
            await send(channel, f"🔨 Building **{ws_key}** [{platform}]...")
            result = await build_platform(platform, ws_path)
            if result.success:
                return await send(channel, f"✅ {platform.upper()} build succeeded.")
            return await send(channel, f"❌ {platform.upper()} build failed:\n```\n{result.error[:1200]}\n```")

        case "demo":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            platform = cmd.platform or "android"
            await send(channel, f"📱 Demoing **{ws_key}** [{platform}]...")
            result = await demo_platform(platform, ws_path)
            msg = result.message
            if result.demo_url:
                msg += f"\n🔗 {result.demo_url}"
            await send(channel, msg, file_path=result.screenshot_path)

        case "deploy":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            platform = cmd.platform or "ios"
            await send(channel, f"📲 Deploying **{ws_key}** to {platform.upper()} device...")
            if platform == "ios":
                result = await deploy_ios(ws_path)
            elif platform == "android":
                result = await deploy_android(ws_path)
            else:
                return await send(channel, f"❌ Deploy supports `ios` or `android`, not `{platform}`.")
            return await send(channel, result.message)

        case "vid":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            await send(channel, f"🎥 Recording **{ws_key}**...")
            ok, msg = await AndroidPlatform.ensure_device()
            if not ok:
                return await send(channel, f"❌ {msg}")
            result = await AndroidPlatform.build(ws_path)
            if not result.success:
                return await send(channel, f"❌ Build failed:\n```\n{result.error[:800]}\n```")
            await AndroidPlatform.launch(ws_path)
            video = await AndroidPlatform.record()
            await send(channel, "✅ Recording captured.", file_path=video)

        case "fix":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            async def fix_status(msg, fpath=None):
                await send(channel, msg, file_path=fpath)
            await fix.handle_fix(cmd.raw_cmd or "", ws_key, ws_path, claude,
                                 on_status=fix_status)

        case "run":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await run_cmd.handle_run(cmd.raw_cmd or "", ws_path))

        case "runsh":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await run_cmd.handle_runsh(cmd.raw_cmd or "", ws_path))

        # ── Git & GitHub ─────────────────────────────────────────
        case "gitstatus":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await git_cmd.handle_status(ws_path, ws_key))

        case "diff":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            full = cmd.sub == "full" if cmd.sub else False
            return await send(channel, await git_cmd.handle_diff(ws_path, full))

        case "commit":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            result = await git_cmd.handle_commit(
                ws_path, ws_key, message=cmd.raw_cmd, claude=claude, auto_push=True)
            return await send(channel, result)

        case "undo":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await git_cmd.handle_undo(ws_path))

        case "gitlog":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            count = int(cmd.raw_cmd) if cmd.raw_cmd and cmd.raw_cmd.isdigit() else 10
            return await send(channel, await git_cmd.handle_log(ws_path, count))

        case "branch":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await git_cmd.handle_branch(ws_path, cmd.raw_cmd))

        case "stash":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await git_cmd.handle_stash(ws_path, pop=(cmd.sub == "pop")))

        case "pr":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await git_cmd.handle_pr(
                ws_path, ws_key, title=cmd.raw_cmd, claude=claude))

        case "repo":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, await git_cmd.handle_repo(
                ws_path, ws_key, sub=cmd.sub, arg=cmd.arg))

        case "mirror":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            from commands.scrcpy import handle_mirror
            return await send(channel, await handle_mirror(cmd.sub or "start"))

        case "memory":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            return await send(channel, memory_cmd.handle_memory(
                cmd.sub, cmd.arg, ws_path, ws_key))

        case "health":
            uptime = int(time.time() - START_TIME)
            m, s = divmod(uptime, 60)
            h, m = divmod(m, 60)
            ws = registry.get_default(message.author.id) or "(none)"
            sess = claude.get_session(ws) or "(none)"
            return await send(channel, (
                f"**Health**\n"
                f"  Uptime: {h}h {m}m {s}s\n"
                f"  Workspace: {ws}\n"
                f"  Session: `{sess[:20]}`\n"
                f"  Agent: {'ON' if config.AGENT_MODE else 'OFF'}\n"
                f"  Workspaces: {len(registry.list_keys())}\n"
                f"  Platforms: Android · iOS · Web"
            ))

        case "reload":
            await send(channel, "♻️ Restarting via pm2…")
            os.system("pm2 restart discord-claude-bridge")

        case "patch-bot":
            if not cmd.raw_cmd:
                return await send(channel, "Usage: `/patch-bot <instructions for changing the bot>`")
            bot_dir = os.path.dirname(os.path.abspath(__file__))
            await send(channel, f"🔧 Patching the bot itself…")

            context = (
                "You are modifying a Discord bot's source code. "
                "The bot is a Python Discord bot using discord.py. "
                "It runs via pm2 which auto-restarts on file changes. "
                "Be surgical — only change what's needed. "
                "After editing, the bot will auto-restart."
            )

            async def patch_progress(msg):
                await send(channel, msg)

            result = await claude.run(
                cmd.raw_cmd, "bot-self", bot_dir,
                context_prefix=context,
                on_progress=patch_progress,
            )
            if result.exit_code != 0 and result.stderr:
                return await send(channel, f"⚠️ Patch error:\n```\n{result.stderr[:1500]}\n```")
            preview = result.stdout[:800] if result.stdout else "(no output)"
            await send(channel, f"✅ Patch applied. Bot will auto-restart.\n```\n{preview}\n```")

        case "newsession":
            ws_key = registry.get_default(message.author.id)
            if ws_key:
                claude.clear_session(ws_key)
                return await send(channel, f"🔄 Fresh session for **{ws_key}**.")
            return await send(channel, "❌ No workspace set.")

        case "unknown":
            return await send(channel, "❓ Unknown command. `/help`")


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client.run(config.DISCORD_BOT_TOKEN)
