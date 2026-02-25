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
from commands import run_cmd, memory_cmd, buildapp, fix, queue, widget
from commands import git_cmd
from commands.bot_todo import handle_bot_todo
from commands.dashboard import handle_dashboard
from cost_tracker import CostTracker
from commands.create import create_kmp_project
from agent_loop import run_agent_loop, format_loop_summary
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
cost_tracker = CostTracker()

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


class ConfirmDeleteView(discord.ui.View):
    """Confirmation buttons for /remove <workspace>."""

    def __init__(self, ws_key: str, ws_path: str, owner_id: int):
        super().__init__(timeout=60)
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.owner_id = owner_id

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        import shutil as _shutil
        try:
            _shutil.rmtree(self.ws_path)
        except Exception as e:
            return await interaction.response.edit_message(
                content=f"Failed to delete `{self.ws_path}`: {e}", view=None)
        registry.remove(self.ws_key)
        await interaction.response.edit_message(
            content=f"Deleted **{self.ws_key}** (`{self.ws_path}`).", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.edit_message(content="Cancelled.", view=None)

    async def on_timeout(self):
        pass


class WorkspaceFooterView(discord.ui.View):
    """Footer with current workspace + Switch button."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id

    @discord.ui.button(label="Switch workspace", style=discord.ButtonStyle.secondary)
    async def switch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        keys = registry.list_keys()
        if not keys:
            return await interaction.response.edit_message(
                content="No workspaces.", view=None)
        view = WorkspaceSelectorView(self.owner_id, keys)
        await interaction.response.edit_message(
            content="Pick a workspace:", view=view)


class WorkspaceSelectorView(discord.ui.View):
    """Shows workspace buttons for switching."""

    def __init__(self, owner_id: int, keys: list[str]):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        current = registry.get_default(owner_id)
        for key in keys[:20]:  # Discord max ~25 buttons
            style = discord.ButtonStyle.primary if key == current else discord.ButtonStyle.secondary
            self.add_item(WorkspaceButton(key, style, owner_id))


class WorkspaceButton(discord.ui.Button):
    """Individual workspace button."""

    def __init__(self, ws_key: str, style: discord.ButtonStyle, owner_id: int):
        super().__init__(label=ws_key, style=style)
        self.ws_key = ws_key
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        registry.set_default(self.owner_id, self.ws_key)
        await interaction.response.edit_message(
            content=f"Switched to **{self.ws_key}**.", view=None)


async def send_workspace_footer(channel, user_id: int):
    """Send workspace footer with Switch button."""
    ws = registry.get_default(user_id)
    if ws:
        view = WorkspaceFooterView(user_id)
        await channel.send(f"📂 workspace: **{ws}**", view=view)


def help_text():
    agent = " *(agent ON)*" if config.AGENT_MODE else " *(agent OFF)*"
    return (
        "**discord-claude-bridge v2 — KMP**" + agent + "\n\n"
        "**Workspace:**\n"
        "`@<ws> <prompt>` — Claude prompt in workspace\n"
        "`/use <ws>` · `/where` · `/workspaces`\n"
        "`/remove <ws>` — delete a workspace\n"
        "`/rename <old> <new>` — rename a workspace\n\n"
        "**Build & Ship (Kotlin Multiplatform):**\n"
        "`/buildapp <description>` — idea → running app\n"
        "`/create <AppName>` — scaffold KMP project\n"
        "`/build android|ios|web` — build a target\n"
        "`/demo android|ios|web` — build + screenshot\n"
        "`/vid` — Android video recording\n"
        "`/fix [instructions]` — auto-fix build errors\n"
        "`/widget <description>` — add iOS home screen widget\n\n"
        "**Mirror & Showcase:**\n"
        "`/mirror start|stop` — emulator in your browser\n"
        "`/showcase <app>` — video demo for everyone *(server)*\n"
        "`/tryapp <app>` — live emulator for anyone *(server)*\n"
        "`/showcase gallery` · `/done`\n\n"
        "**Queue:**\n"
        "`/queue task1 --- task2 --- ...` — run tasks overnight\n"
        "`/spend` — check daily spend & remaining budget\n\n"
        "**Dashboard:**\n"
        "`/dashboard` — iPhone-style launcher for all apps\n"
        "`/dashboard rebuild` — force rebuild all web apps\n\n"
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
        "**Bot Todos:**\n"
        "`/bot-todo <note>` — add a todo\n"
        "`/bot-todo` — list todos\n"
        "`/bot-todo done <N>` — mark done\n\n"
        "**System:**\n"
        "`/health` · `/reload` · `/newsession`"
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
        cost_tracker.add(result.total_cost_usd)
        if result.exit_code != 0 and result.stderr:
            # Auto-reset session on context compaction crash so next message works
            if "chunk" in result.stderr and "limit" in result.stderr:
                claude.clear_session(ws_key)
                return await send(channel,
                    "⚠️ Session too large — context compaction crashed.\n"
                    "Session has been auto-reset. Please resend your message.")
            return await send(channel, f"⚠️ Error:\n```\n{result.stderr[:1500]}\n```")
        await send(channel, result.stdout or "(empty)")

        # Auto-build web so iPhone users can see updates immediately
        if config.AGENT_MODE:
            await send(channel, "🌐 Auto-building web...")
            web_result = await build_platform("web", ws_path)
            if web_result.success:
                url = await WebPlatform.serve(ws_path)
                if url:
                    await send(channel, f"✅ Web build succeeded → {url}")
                else:
                    await send(channel, "✅ Web build succeeded (no dist dir found).")
            else:
                await send(channel, "⚠️ Web build failed — auto-fixing...")
                async def web_fix_status(msg):
                    await send(channel, msg)
                fix_result = await run_agent_loop(
                    initial_prompt=(
                        "The wasmJs web build failed. Fix the code so it compiles for web.\n"
                        "Only modify what's necessary for web compatibility.\n\n"
                        f"```\n{web_result.error[:800]}\n```"
                    ),
                    workspace_key=ws_key,
                    workspace_path=ws_path,
                    claude=claude,
                    platform="web",
                    max_attempts=2,
                    on_status=web_fix_status,
                )
                summary = format_loop_summary(fix_result)
                await send(channel, summary)
                if fix_result.success:
                    url = await WebPlatform.serve(ws_path)
                    if url:
                        await send(channel, f"✅ Web fixed → {url}")
        await send_workspace_footer(channel, message.author.id)
        return

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
                return await send(channel, "Usage: `/remove <workspace>`")
            ws_key = cmd.workspace.lower()
            ws_path = registry.get_path(ws_key)
            if not ws_path:
                return await send(channel, f"❌ Unknown workspace: `{ws_key}`")

            view = ConfirmDeleteView(ws_key, ws_path, message.author.id)
            await channel.send(
                f"Delete **{ws_key}** (`{ws_path}`)?\nThis removes all files permanently.",
                view=view,
            )
            return

        case "rename":
            if not cmd.workspace or not cmd.arg:
                return await send(channel, "Usage: `/rename <old-name> <new-name>`")
            old_key = cmd.workspace.lower()
            new_key = cmd.arg.lower()
            if not registry.get_path(old_key):
                return await send(channel, f"❌ Workspace `{old_key}` not found.")
            if registry.get_path(new_key):
                return await send(channel, f"❌ `{new_key}` already exists.")
            if registry.rename(old_key, new_key):
                return await send(channel, f"Renamed **{old_key}** → **{new_key}**")
            return await send(channel, f"❌ Could not rename `{old_key}`.")

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

        case "widget":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            async def widget_status(msg, fpath=None):
                await send(channel, msg, file_path=fpath)
            await widget.handle_widget(cmd.raw_cmd or "", ws_key, ws_path, claude,
                                       on_status=widget_status)

        case "queue":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            if not cmd.raw_cmd:
                return await send(channel, "Usage: `/queue task1 --- task2 --- task3`")
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                return await send(channel, "❌ No workspace set.")
            async def queue_status(msg, fpath=None):
                await send(channel, msg, file_path=fpath)
            await queue.handle_queue(
                cmd.raw_cmd, ws_key, ws_path, claude, cost_tracker,
                on_status=queue_status,
            )

        case "spend":
            spent = cost_tracker.today_spent()
            cap = config.DAILY_TOKEN_CAP_USD
            tasks = cost_tracker.today_tasks()
            remaining = max(0, cap * (config.QUEUE_STOP_PCT / 100.0) - spent)
            return await send(channel, (
                f"💰 **Daily Spend**\n"
                f"  Today: ${spent:.4f}\n"
                f"  Budget: ${cap:.2f} ({config.QUEUE_STOP_PCT}% cap)\n"
                f"  Remaining: ${remaining:.2f}\n"
                f"  Tasks: {tasks}"
            ))

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
            return await send(channel,
                "`/patch-bot` is retired. Use `/bot-todo <note>` to track bot improvements.")

        case "bot-todo":
            return await send(channel, handle_bot_todo(cmd.raw_cmd))

        case "dashboard":
            if not config.AGENT_MODE:
                return await send(channel, "🔒 Agent mode OFF.")
            async def dash_status(msg, fpath=None):
                await send(channel, msg, file_path=fpath)
            await handle_dashboard(
                registry, dash_status, rebuild=(cmd.sub == "rebuild"),
            )

        case "newsession":
            ws_key = registry.get_default(message.author.id)
            if ws_key:
                claude.clear_session(ws_key)
                return await send(channel, f"🔄 Fresh session for **{ws_key}**.")
            return await send(channel, "❌ No workspace set.")

        case "unknown":
            return await send(channel, "❓ Unknown command. `/help`")

    # Workspace footer — reminds user which workspace they're in + switch button
    await send_workspace_footer(channel, message.author.id)


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client.run(config.DISCORD_BOT_TOKEN)
