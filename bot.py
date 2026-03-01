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
from commands import run_cmd, memory_cmd, buildapp, fix, queue, widget, fixes_cmd
from commands import git_cmd
from commands.bot_todo import handle_bot_todo
from commands.dashboard import handle_dashboard
from commands.testflight import handle_testflight
from cost_tracker import CostTracker
from commands.create import create_kmp_project
from agent_loop import run_agent_loop, format_loop_summary
from platforms import demo_platform, build_platform, deploy_ios, deploy_android, AndroidPlatform, iOSPlatform, WebPlatform
from supabase_client import snapshot_sql_files, detect_changed_sql, sync_sql_files

# ── Startup ──────────────────────────────────────────────────────────────────

START_TIME = time.time()

# ── Maintenance mode (runtime toggle, not persisted across restarts) ─────
maintenance_mode: bool = False
maintenance_message: str = "🔧 Bot is under maintenance — back shortly!"

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


class EditSaveDescriptionModal(discord.ui.Modal, title="Edit save description"):
    """Modal to edit the auto-generated save description before committing."""

    description_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.long,
        max_length=500,
        placeholder="e.g. added dark mode toggle",
    )

    def __init__(self, view: "SaveConfirmView"):
        super().__init__()
        self.save_view = view
        self.description_input.default = view.description

    async def on_submit(self, interaction: discord.Interaction):
        new_desc = self.description_input.value.strip()[:500]
        self.save_view.stop()
        result = await git_cmd.commit_save(
            self.save_view.ws_path, self.save_view.save_number, new_desc,
        )
        await interaction.response.edit_message(content=result, view=None)


class SaveConfirmView(discord.ui.View):
    """Preview save description with Save / Edit buttons."""

    def __init__(self, ws_path: str, owner_id: int, save_number: int, description: str):
        super().__init__(timeout=60)
        self.ws_path = ws_path
        self.owner_id = owner_id
        self.save_number = save_number
        self.description = description

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        result = await git_cmd.commit_save(self.ws_path, self.save_number, self.description)
        await interaction.response.edit_message(content=result, view=None)

    @discord.ui.button(label="Edit description", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(EditSaveDescriptionModal(self))

    async def on_timeout(self):
        # Unstage changes so nothing is left half-committed
        await git_cmd._git(["reset", "HEAD"], self.ws_path)
        try:
            await self.message.edit(
                content="⏰ Save cancelled (timed out). Your changes are still there — run `/save` again.",
                view=None,
            )
        except Exception:
            pass


class SaveListView(discord.ui.View):
    """Save history with a dropdown to load any previous save."""

    def __init__(self, owner_id: int, ws_path: str, saves: list[tuple[int, str, str]]):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.ws_path = ws_path
        self.selected_num = None
        # Build select options (max 25 in a Select)
        select = discord.ui.Select(
            placeholder="Load a previous save…",
            min_values=1, max_values=1,
        )
        for num, desc, date in saves[:25]:
            rel = git_cmd._relative_date(date)
            label = f"Save {num}"
            select.append_option(discord.SelectOption(
                label=label,
                description=f"{desc[:50]} — {rel}",
                value=str(num),
            ))
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.selected_num = int(interaction.data["values"][0])
        # Show confirmation
        self.clear_items()
        self.add_item(ConfirmLoadButton(self))
        self.add_item(CancelLoadButton(self))
        await interaction.response.edit_message(
            content=f"⏪ Load **Save {self.selected_num}**? This creates a new save with that version's files.",
            view=self,
        )

    async def on_timeout(self):
        try:
            await self.message.edit(view=None)
        except Exception:
            pass


class ConfirmLoadButton(discord.ui.Button):
    def __init__(self, parent: SaveListView):
        super().__init__(label="Load", style=discord.ButtonStyle.success)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.parent_view.stop()
        result = await git_cmd.load_save(self.parent_view.ws_path, self.parent_view.selected_num)
        await interaction.response.edit_message(content=result, view=None)


class CancelLoadButton(discord.ui.Button):
    def __init__(self, parent: SaveListView):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.parent_view.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class WorkspaceSelectorView(discord.ui.View):
    """Shows workspace buttons for switching."""

    def __init__(self, owner_id: int, keys: list[str]):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.footer_message = None  # set after footer is sent, so button can edit it
        current = registry.get_default(owner_id)
        for key in keys[:20]:
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
        # Update the workspace footer below if it exists
        if self.view and hasattr(self.view, 'footer_message') and self.view.footer_message:
            try:
                await self.view.footer_message.edit(content=f"📂 workspace: **{self.ws_key}**")
            except Exception:
                pass


# ── Data-interview guard ─────────────────────────────────────────────────
# Tracks (channel_id, user_id) pairs with a pending data-description question
# so the reply isn't double-processed as a FallbackPrompt.
_interview_pending: set[tuple[int, int]] = set()


class SkipDataInterviewView(discord.ui.View):
    """Single 'Skip' button for the data-modeling interview."""

    def __init__(self):
        super().__init__(timeout=120)
        self.skipped = False

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.skipped = True
        self.stop()
        await interaction.response.defer()


STILL_LISTENING = "💡 *I'm still listening — feel free to send other commands while this runs.*"


async def _run_demo(channel, ws_key: str, ws_path: str, platform: str):
    """Run a demo for a single platform. Shared by /demo <plat> and DemoPlatformView."""
    await send(channel, f"📱 Demoing **{ws_key}** [{platform}]...")
    await send(channel, STILL_LISTENING)

    if platform == "ios":
        await send(channel, "Booting iOS Simulator...")
        ok, sim_msg = await iOSPlatform.ensure_simulator()
        if not ok:
            await send(channel, f"❌ {sim_msg}")
        else:
            await send(channel, f"{sim_msg} Building KMP framework + Xcode project...")
            build_result = await iOSPlatform.build(ws_path)

            # Auto-fix: if build fails, use agent loop (same as /buildapp iOS)
            if not build_result.success:
                await send(channel, "⚠️ iOS build failed — auto-fixing...")

                async def ios_fix_status(msg):
                    await send(channel, msg)

                fix_result = await run_agent_loop(
                    initial_prompt=(
                        "The iOS build failed. Fix the code so it compiles for iOS.\n"
                        "Only modify what's necessary for iOS compatibility.\n"
                        f"IMPORTANT: When running xcodebuild, always use: -destination 'name={config.IOS_SIMULATOR_NAME}'\n"
                        "NEVER use 'simctl launch --console' — it blocks forever. Use 'simctl launch' without --console.\n\n"
                        f"```\n{build_result.error[:800]}\n```"
                    ),
                    workspace_key=ws_key,
                    workspace_path=ws_path,
                    claude=claude,
                    platform="ios",
                    max_attempts=config.MAX_BUILD_ATTEMPTS,
                    on_status=ios_fix_status,
                )
                if not fix_result.success:
                    summary = format_loop_summary(fix_result)
                    await send(channel, summary)
                    build_result = None
                else:
                    await send(channel, "✅ iOS build fixed!")
                    try:
                        fixes_cmd.log_fix(ws_path, "ios", build_result.error[:300] if build_result.error else "Build error",
                                          "Auto-fixed iOS build failure")
                    except Exception:
                        pass

            if build_result is None:
                pass  # auto-fix failed, already reported
            else:
                await send(channel, "Build succeeded. Installing on simulator...")
                bundle_id = await iOSPlatform.install_and_launch(ws_path)
                if bundle_id.startswith(("Could not", "Install failed", "Installed but")):
                    await send(channel, f"❌ {bundle_id}")
                else:
                    await send(channel, f"Launched **{bundle_id}**. Checking for crashes...")
                    await asyncio.sleep(3)

                    # Check for runtime crash
                    crash_log = await iOSPlatform.check_crash(bundle_id)
                    if crash_log:
                        await send(channel, "💥 App crashed on launch — auto-fixing...")
                        async def crash_fix_status(msg):
                            await send(channel, msg)

                        crash_fixed = False
                        for crash_attempt in range(1, config.MAX_BUILD_ATTEMPTS + 1):
                            fix_result = await run_agent_loop(
                                initial_prompt=(
                                    f"The iOS app ({bundle_id}) crashes on launch with a runtime error.\n"
                                    "Fix the code so it runs without crashing.\n"
                                    f"IMPORTANT: When running xcodebuild, always use: -destination 'name={config.IOS_SIMULATOR_NAME}'\n"
                                    "NEVER use 'simctl launch --console' — it blocks forever. Use 'simctl launch' without --console.\n\n"
                                    f"Crash log:\n```\n{crash_log[:800]}\n```"
                                ),
                                workspace_key=ws_key,
                                workspace_path=ws_path,
                                claude=claude,
                                platform="ios",
                                max_attempts=config.MAX_BUILD_ATTEMPTS,
                                on_status=crash_fix_status,
                            )
                            if not fix_result.success:
                                await send(channel, format_loop_summary(fix_result))
                                break

                            # Rebuild succeeded — try launching again
                            bundle_id = await iOSPlatform.install_and_launch(ws_path)
                            if bundle_id.startswith(("Could not", "Install failed", "Installed but")):
                                await send(channel, f"❌ {bundle_id}")
                                break

                            await asyncio.sleep(3)
                            crash_log = await iOSPlatform.check_crash(bundle_id)
                            if not crash_log:
                                crash_fixed = True
                                break
                            await send(channel, f"💥 Still crashing (attempt {crash_attempt})— retrying fix...")

                        if crash_fixed:
                            await send(channel, "✅ Crash fixed!")
                            try:
                                fixes_cmd.log_fix(ws_path, "ios", f"Runtime crash: {crash_log[:300]}",
                                                  "Fixed crash-on-launch")
                            except Exception:
                                pass
                        else:
                            if not crash_log:
                                pass  # already reported above
                            else:
                                await send(channel, f"❌ App still crashing after {config.MAX_BUILD_ATTEMPTS} fix attempts.")
                            return

                    # App is running — take screenshot
                    screenshot = await iOSPlatform.screenshot()
                    await send(channel, f"✅ **{bundle_id}** running on iOS Simulator.", file_path=screenshot)
    elif platform == "android":
        await send(channel, "Checking Android device/emulator...")
        ok, dev_msg = await AndroidPlatform.ensure_device()
        if not ok:
            await send(channel, f"❌ {dev_msg}")
        else:
            await send(channel, f"{dev_msg} Building Android APK...")
            build_result = await AndroidPlatform.build(ws_path)

            # Auto-fix: if build fails, use agent loop
            if not build_result.success:
                await send(channel, "⚠️ Android build failed — auto-fixing...")

                async def android_fix_status(msg):
                    await send(channel, msg)

                fix_result = await run_agent_loop(
                    initial_prompt=(
                        "The Android build failed. Fix the code so it compiles for Android.\n"
                        "Only modify what's necessary for Android compatibility.\n\n"
                        f"```\n{build_result.error[:800]}\n```"
                    ),
                    workspace_key=ws_key,
                    workspace_path=ws_path,
                    claude=claude,
                    platform="android",
                    max_attempts=config.MAX_BUILD_ATTEMPTS,
                    on_status=android_fix_status,
                )
                if not fix_result.success:
                    summary = format_loop_summary(fix_result)
                    await send(channel, summary)
                    build_result = None
                else:
                    await send(channel, "✅ Android build fixed!")
                    try:
                        fixes_cmd.log_fix(ws_path, "android", build_result.error[:300] if build_result.error else "Build error",
                                          "Auto-fixed Android build failure")
                    except Exception:
                        pass

            if build_result is None:
                pass  # auto-fix failed, already reported
            else:
                await send(channel, "Build succeeded. Installing on device...")
                install_result = await AndroidPlatform.install(ws_path)
                if not install_result.success:
                    await send(channel, f"❌ Install failed:\n```\n{install_result.error[:800]}\n```")
                else:
                    await AndroidPlatform.clear_logcat()
                    app_id = await AndroidPlatform.launch(ws_path)
                    if app_id.startswith("Could not"):
                        await send(channel, f"❌ {app_id}")
                    else:
                        await send(channel, f"Launched **{app_id}**. Checking for crashes...")
                        await asyncio.sleep(3)

                        crash_log = await AndroidPlatform.check_crash(app_id)
                        if crash_log:
                            await send(channel, "💥 App crashed on launch — auto-fixing...")
                            async def android_crash_fix_status(msg):
                                await send(channel, msg)

                            crash_fixed = False
                            for crash_attempt in range(1, config.MAX_BUILD_ATTEMPTS + 1):
                                fix_result = await run_agent_loop(
                                    initial_prompt=(
                                        f"The Android app ({app_id}) crashes on launch with a runtime error.\n"
                                        "Fix the code so it runs without crashing.\n\n"
                                        f"Crash log (from logcat):\n```\n{crash_log[:800]}\n```"
                                    ),
                                    workspace_key=ws_key,
                                    workspace_path=ws_path,
                                    claude=claude,
                                    platform="android",
                                    max_attempts=config.MAX_BUILD_ATTEMPTS,
                                    on_status=android_crash_fix_status,
                                )
                                if not fix_result.success:
                                    await send(channel, format_loop_summary(fix_result))
                                    break

                                # Rebuild + reinstall + relaunch
                                install_result = await AndroidPlatform.install(ws_path)
                                if not install_result.success:
                                    await send(channel, f"❌ Reinstall failed:\n```\n{install_result.error[:800]}\n```")
                                    break

                                await AndroidPlatform.clear_logcat()
                                app_id = await AndroidPlatform.launch(ws_path)
                                if app_id.startswith("Could not"):
                                    await send(channel, f"❌ {app_id}")
                                    break

                                await asyncio.sleep(3)
                                crash_log = await AndroidPlatform.check_crash(app_id)
                                if not crash_log:
                                    crash_fixed = True
                                    break
                                await send(channel, f"💥 Still crashing (attempt {crash_attempt}) — retrying fix...")

                            if crash_fixed:
                                await send(channel, "✅ Crash fixed!")
                                try:
                                    fixes_cmd.log_fix(ws_path, "android", f"Runtime crash: {crash_log[:300]}",
                                                      "Fixed crash-on-launch")
                                except Exception:
                                    pass
                            else:
                                if not crash_log:
                                    pass
                                else:
                                    await send(channel, f"❌ App still crashing after {config.MAX_BUILD_ATTEMPTS} fix attempts.")
                                return

                        # App is running — take screenshot
                        screenshot = await AndroidPlatform.screenshot()
                        await send(channel, f"✅ **{app_id}** running on Android.", file_path=screenshot)

    elif platform == "web":
        await send(channel, "Building web app...")
        build_result = await WebPlatform.build(ws_path)

        # Auto-fix: if build fails, use agent loop
        if not build_result.success:
            await send(channel, "⚠️ Web build failed — auto-fixing...")

            async def web_fix_status(msg):
                await send(channel, msg)

            fix_result = await run_agent_loop(
                initial_prompt=(
                    "The Web (WASM/JS) build failed. Fix the code so it compiles for web.\n"
                    "Only modify what's necessary for web compatibility.\n\n"
                    f"```\n{build_result.error[:800]}\n```"
                ),
                workspace_key=ws_key,
                workspace_path=ws_path,
                claude=claude,
                platform="web",
                max_attempts=config.MAX_BUILD_ATTEMPTS,
                on_status=web_fix_status,
            )
            if not fix_result.success:
                summary = format_loop_summary(fix_result)
                await send(channel, summary)
                build_result = None
            else:
                await send(channel, "✅ Web build fixed!")
                try:
                    fixes_cmd.log_fix(ws_path, "web", build_result.error[:300] if build_result.error else "Build error",
                                      "Auto-fixed Web build failure")
                except Exception:
                    pass

        if build_result is None:
            pass  # auto-fix failed, already reported
        else:
            await send(channel, "Build succeeded. Starting web server...")
            url = await WebPlatform.serve(ws_path)
            if not url:
                await send(channel, "❌ Built but could not find distribution directory.")
            else:
                await asyncio.sleep(2)
                health_err = await WebPlatform.check_health(url)
                if health_err:
                    await send(channel, f"⚠️ Web app unhealthy ({health_err}) — auto-fixing...")
                    async def web_health_fix_status(msg):
                        await send(channel, msg)

                    health_fixed = False
                    for health_attempt in range(1, config.MAX_BUILD_ATTEMPTS + 1):
                        fix_result = await run_agent_loop(
                            initial_prompt=(
                                f"The web app built and is being served at {url}, but the health check failed.\n"
                                "Fix the code so the web app loads correctly in a browser.\n\n"
                                f"Health check error:\n```\n{health_err}\n```"
                            ),
                            workspace_key=ws_key,
                            workspace_path=ws_path,
                            claude=claude,
                            platform="web",
                            max_attempts=config.MAX_BUILD_ATTEMPTS,
                            on_status=web_health_fix_status,
                        )
                        if not fix_result.success:
                            await send(channel, format_loop_summary(fix_result))
                            break

                        # Rebuild + re-serve + re-check
                        rebuild = await WebPlatform.build(ws_path)
                        if not rebuild.success:
                            await send(channel, f"❌ Rebuild failed:\n```\n{rebuild.error[:800]}\n```")
                            break

                        url = await WebPlatform.serve(ws_path)
                        if not url:
                            await send(channel, "❌ Could not find distribution directory after rebuild.")
                            break

                        await asyncio.sleep(2)
                        health_err = await WebPlatform.check_health(url)
                        if not health_err:
                            health_fixed = True
                            break
                        await send(channel, f"⚠️ Still unhealthy (attempt {health_attempt}) — retrying fix...")

                    if health_fixed:
                        await send(channel, "✅ Web app healthy!")
                        try:
                            fixes_cmd.log_fix(ws_path, "web", f"Health check: {health_err or 'failed'}",
                                              "Fixed web health check")
                        except Exception:
                            pass
                    else:
                        if not health_err:
                            pass
                        else:
                            await send(channel, f"❌ Web app still unhealthy after {config.MAX_BUILD_ATTEMPTS} fix attempts.")
                        return

                await send(channel, f"✅ Web app live!\n🔗 {url}")

    else:
        result = await demo_platform(platform, ws_path)
        msg = result.message
        if result.demo_url:
            msg += f"\n🔗 {result.demo_url}"
        await send(channel, msg, file_path=result.screenshot_path)


class DemoPlatformView(discord.ui.View):
    """Platform picker buttons for /demo."""

    def __init__(self, owner_id: int, ws_key: str, ws_path: str):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.ws_key = ws_key
        self.ws_path = ws_path

    @discord.ui.button(label="Android", style=discord.ButtonStyle.success, emoji="📱")
    async def android(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        await _run_demo(interaction.channel, self.ws_key, self.ws_path, "android")

    @discord.ui.button(label="iOS", style=discord.ButtonStyle.primary, emoji="🍎")
    async def ios(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        await _run_demo(interaction.channel, self.ws_key, self.ws_path, "ios")

    @discord.ui.button(label="Web", style=discord.ButtonStyle.secondary, emoji="🌐")
    async def web(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(view=None)
        await _run_demo(interaction.channel, self.ws_key, self.ws_path, "web")


class AddQueueTaskModal(discord.ui.Modal, title="Add a task"):
    """Modal popup with a text field for one queue task."""

    task = discord.ui.TextInput(
        label="Task description",
        style=discord.TextStyle.long,
        max_length=500,
        placeholder="e.g. add dark mode support",
    )

    def __init__(self, view: "QueueBuilderView"):
        super().__init__()
        self.queue_view = view

    async def on_submit(self, interaction: discord.Interaction):
        self.queue_view.tasks.append(self.task.value)
        await interaction.response.edit_message(
            content=self.queue_view.build_message(),
            view=self.queue_view,
        )


class QueueBuilderView(discord.ui.View):
    """Interactive wizard: add tasks one at a time, then start the queue."""

    def __init__(self, owner_id: int, channel, ws_key: str, ws_path: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.channel = channel
        self.ws_key = ws_key
        self.ws_path = ws_path
        self.tasks: list[str] = []

    def build_message(self) -> str:
        count = len(self.tasks)
        label = "task" if count == 1 else "tasks"
        header = f"📋 **Queue Builder** — {count} {label}"
        if not self.tasks:
            return header
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(self.tasks, 1))
        return f"{header}\n{listing}"

    @discord.ui.button(label="Add task", style=discord.ButtonStyle.primary)
    async def add_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.send_modal(AddQueueTaskModal(self))

    @discord.ui.button(label="Start queue ▶️", style=discord.ButtonStyle.success)
    async def start_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        if not self.tasks:
            return await interaction.response.send_message("Add at least one task first.", ephemeral=True)
        # Disable buttons and update message
        self.stop()
        await interaction.response.edit_message(
            content=self.build_message() + "\n\n▶️ *Queue started…*",
            view=None,
        )
        # Kick off the queue
        raw = " --- ".join(self.tasks)

        async def queue_status(msg, fpath=None):
            await send(self.channel, msg, file_path=fpath)

        await queue.handle_queue(
            raw, self.ws_key, self.ws_path, claude, cost_tracker,
            on_status=queue_status,
        )

    async def on_timeout(self):
        pass


async def send_workspace_footer(channel, user_id: int, selector_view=None):
    """Send plain-text workspace indicator, or selector buttons when none is set."""
    ws = registry.get_default(user_id)
    if ws:
        msg = await channel.send(f"📂 workspace: **{ws}**")
        # Link footer to selector so button click can edit it
        if selector_view is not None:
            selector_view.footer_message = msg
    else:
        keys = registry.list_keys()
        if keys:
            view = WorkspaceSelectorView(user_id, keys)
            await channel.send("📂 No workspace set — pick one:", view=view)


def help_text():
    agent = " *(agent ON)*" if config.AGENT_MODE else " *(agent OFF)*"
    return (
        "**discord-claude-bridge** — build apps from chat" + agent + "\n\n"
        "**Build Apps:**\n"
        "`/build app <description>` — idea → running app\n"
        "`/build android|ios|web` — build one platform\n"
        "`/demo android|ios|web` — build + screenshot\n"
        "`/fix [instructions]` — auto-fix build errors\n"
        "`/testflight` — upload to TestFlight\n"
        "`/deploy ios|android` — install on device\n"
        "`/vid` — record Android emulator\n"
        "`/widget <desc>` — add iOS widget\n\n"
        "**Workspaces:**\n"
        "`@<ws> <prompt>` — talk to Claude in a workspace\n"
        "`/use <ws>` · `/ls` — switch / list workspaces\n"
        "`/create <Name>` — scaffold new project\n"
        "`/remove <ws>` · `/rename <old> <new>`\n\n"
        "**Save:**\n"
        "`/save` — save your progress (all platforms)\n"
        "`/save list` — see your save history\n"
        "`/save undo` · `/save redo`\n"
        "`/save github` — upload to GitHub\n\n"
        "**Git:**\n"
        "`/status` · `/diff` · `/commit [msg]` · `/log`\n"
        "`/branch [name]` · `/stash` · `/pr [title]`\n"
        "`/undo` · `/repo`\n\n"
        "**Tools:**\n"
        "`/run <cmd>` — run shell command in workspace\n"
        "`/queue task1 --- task2` — batch tasks\n"
        "`/spend` — daily budget\n"
        "`/dashboard` — web launcher for all apps\n"
        "`/bot-todo` — track improvements\n"
        "`/memory show|pin|reset` — project memory\n"
        "`/fixes show|clear` — build fix log\n\n"
        "**System:**\n"
        "`/setup` · `/health` · `/reload` · `/newsession`\n\n"
        "**Owner Only:**\n"
        "`/maintenance` — block public commands while updating\n"
        "`/maintenance <msg>` — custom maintenance message\n"
        "`/maintenance off` — resume public access\n"
        "`/announce <msg>` — post to announcement channel"
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
    # Brief delay so rapid reconnect bursts settle
    await asyncio.sleep(3)
    # DM the owner on startup
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
async def on_message(message: discord.Message):
    global maintenance_mode, maintenance_message
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
        if maintenance_mode and not is_owner:
            return await send(channel, maintenance_message)
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
        # If a data-interview is pending for this user, let the interview
        # collector grab the reply instead of routing it to Claude.
        if isinstance(parsed, FallbackPrompt) and (channel.id, message.author.id) in _interview_pending:
            return

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
        await send(channel, STILL_LISTENING)

        # Snapshot SQL files before Claude runs (for auto-sync)
        sql_before = {}
        if config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
            sql_before = snapshot_sql_files(ws_path)

        async def claude_progress(msg):
            await send(channel, msg)

        result = await claude.run(prompt, ws_key, ws_path, on_progress=claude_progress)
        cost_tracker.add(result.total_cost_usd)
        if result.exit_code != 0:
            error_detail = result.stderr.strip() or result.stdout.strip() or ""
            # Auto-reset session on context compaction crash so next message works
            if error_detail and "chunk" in error_detail and "limit" in error_detail:
                claude.clear_session(ws_key)
                return await send(channel,
                    "⚠️ Session too large — context compaction crashed.\n"
                    "Session has been auto-reset. Please resend your message.")
            # Retry once on transient failures
            if not error_detail or "timeout" in error_detail.lower():
                claude.clear_session(ws_key)
                await send(channel, "⚠️ Claude failed, retrying...")
                result = await claude.run(prompt, ws_key, ws_path, on_progress=claude_progress)
                cost_tracker.add(result.total_cost_usd)
                if result.exit_code != 0:
                    error_detail = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    return await send(channel, f"⚠️ Claude failed:\n```\n{error_detail[:1500]}\n```")
            else:
                return await send(channel, f"⚠️ Error:\n```\n{error_detail[:1500]}\n```")
        await send(channel, result.stdout or "(empty)")

        # Auto-sync changed SQL files to Supabase
        if sql_before and config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
            changed_sql = detect_changed_sql(sql_before, ws_path)
            if changed_sql:
                await send(channel, "🗄️ Updating database...")
                ok, sync_msg = await sync_sql_files(changed_sql)
                icon = "✅" if ok else "⚠️"
                await send(channel, f"{icon} {sync_msg}")

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
    _active_selector_view = None  # track WorkspaceSelectorView for footer linking

    match cmd.name:
        case "help":
            await send(channel, help_text())

        case "ls":
            keys = registry.list_keys()
            if keys:
                view = WorkspaceSelectorView(message.author.id, keys)
                await channel.send("**Workspaces:**", view=view)
                _active_selector_view = view
            else:
                await send(channel, "No workspaces.")

        case "use":
            if not cmd.workspace:
                await send(channel, "Usage: `/use <workspace>`")
            elif registry.set_default(message.author.id, cmd.workspace):
                await send(channel, f"✅ Default → **{cmd.workspace}**")
            else:
                await send(channel, f"❌ Unknown: `{cmd.workspace}`")

        case "where":
            # Redundant with workspace footer, but keep for backwards compat
            ws = registry.get_default(message.author.id)
            if ws:
                await send(channel, f"📂 **{ws}** → `{registry.get_path(ws)}`")
            else:
                await send(channel, "No default set.")

        case "create":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            elif not cmd.app_name:
                await send(channel, "Usage: `/create <AppName>`")
            else:
                result = await create_kmp_project(cmd.app_name, registry)
                await send(channel, result.message)

        case "deleteapp":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            elif not cmd.workspace:
                await send(channel, "Usage: `/remove <workspace>`")
            else:
                ws_key = cmd.workspace.lower()
                ws_path = registry.get_path(ws_key)
                if not ws_path:
                    await send(channel, f"❌ Unknown workspace: `{ws_key}`")
                else:
                    view = ConfirmDeleteView(ws_key, ws_path, message.author.id)
                    await channel.send(
                        f"Delete **{ws_key}** (`{ws_path}`)?\nThis removes all files permanently.",
                        view=view,
                    )

        case "rename":
            if not cmd.workspace or not cmd.arg:
                await send(channel, "Usage: `/rename <old-name> <new-name>`")
            else:
                old_key = cmd.workspace.lower()
                new_key = cmd.arg.lower()
                if not registry.get_path(old_key):
                    await send(channel, f"❌ Workspace `{old_key}` not found.")
                elif registry.get_path(new_key):
                    await send(channel, f"❌ `{new_key}` already exists.")
                elif registry.rename(old_key, new_key):
                    await send(channel, f"Renamed **{old_key}** → **{new_key}**")
                else:
                    await send(channel, f"❌ Could not rename `{old_key}`.")

        case "buildapp":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                async def ba_status(msg, fpath=None):
                    await send(channel, msg, file_path=fpath)

                async def ba_ask(question: str) -> str | None:
                    """Ask the user a question during buildapp; return their reply or None on skip/timeout."""
                    view = SkipDataInterviewView()
                    q_msg = await channel.send(question, view=view)
                    pair = (channel.id, message.author.id)
                    _interview_pending.add(pair)

                    def check(m: discord.Message) -> bool:
                        return (
                            m.channel.id == channel.id
                            and m.author.id == message.author.id
                            and not m.content.startswith("/")
                            and not m.content.startswith("@")
                        )

                    try:
                        wait_msg = asyncio.ensure_future(
                            client.wait_for("message", check=check, timeout=120)
                        )
                        wait_skip = asyncio.ensure_future(view.wait())
                        done, pending = await asyncio.wait(
                            {wait_msg, wait_skip}, return_when=asyncio.FIRST_COMPLETED
                        )
                        for t in pending:
                            t.cancel()

                        if wait_msg in done:
                            reply = wait_msg.result()
                            view.stop()
                            await q_msg.edit(view=None)
                            return reply.content.strip() or None
                        # Skip button pressed or timeout
                        await q_msg.edit(view=None)
                        return None
                    except asyncio.TimeoutError:
                        await q_msg.edit(view=None)
                        return None
                    finally:
                        _interview_pending.discard(pair)

                slug = await buildapp.handle_buildapp(
                    cmd.raw_cmd or "", registry, claude, ba_status, on_ask=ba_ask
                )
                if slug:
                    registry.set_default(message.author.id, slug)
                    await send(channel, f"📂 Switched to **{slug}**")

        case "build":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    platform = cmd.platform or "android"
                    await send(channel, f"🔨 Building **{ws_key}** [{platform}]...")
                    await send(channel, STILL_LISTENING)
                    result = await build_platform(platform, ws_path)
                    if result.success:
                        await send(channel, f"✅ {platform.upper()} build succeeded.")
                    else:
                        await send(channel, f"❌ {platform.upper()} build failed:\n```\n{result.error[:1200]}\n```")

        case "platform":
            if cmd.platform and cmd.platform in ("ios", "android", "web"):
                registry.set_platform(message.author.id, cmd.platform)
                reply = f"✅ Default demo platform set to **{cmd.platform}**."
                if cmd.platform == "ios":
                    reply += "\n💡 For native iOS builds, you'll need an Apple Developer account ($99/yr). Use `/testflight` to distribute to your phone."
                await send(channel, reply)
            elif cmd.platform:
                await send(channel, "❌ Unknown platform. Use `/platform ios`, `android`, or `web`.")
            else:
                current = registry.get_platform(message.author.id)
                await send(channel, f"📱 Your demo platform: **{current or 'web (default)'}**\nChange with `/platform ios`, `android`, or `web`.")

        case "demo":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                elif cmd.platform:
                    # /demo android, /demo ios, /demo web → run directly
                    await _run_demo(channel, ws_key, ws_path, cmd.platform)
                else:
                    # /demo → auto-pick from preference, default to web
                    platform = registry.get_platform(message.author.id) or "web"
                    await _run_demo(channel, ws_key, ws_path, platform)

        case "deploy":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    platform = cmd.platform or "ios"
                    await send(channel, f"📲 Deploying **{ws_key}** to {platform.upper()} device...")
                    if platform == "ios":
                        result = await deploy_ios(ws_path)
                        await send(channel, result.message)
                    elif platform == "android":
                        result = await deploy_android(ws_path)
                        await send(channel, result.message)
                    else:
                        await send(channel, f"❌ Deploy supports `ios` or `android`, not `{platform}`.")

        case "testflight":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    async def tf_status(msg, fpath=None):
                        await send(channel, msg, file_path=fpath)
                    await handle_testflight(ws_key, ws_path, on_status=tf_status)

        case "vid":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    await send(channel, f"🎥 Recording **{ws_key}**...")
                    ok, msg = await AndroidPlatform.ensure_device()
                    if not ok:
                        await send(channel, f"❌ {msg}")
                    else:
                        result = await AndroidPlatform.build(ws_path)
                        if not result.success:
                            await send(channel, f"❌ Build failed:\n```\n{result.error[:800]}\n```")
                        else:
                            await AndroidPlatform.launch(ws_path)
                            video = await AndroidPlatform.record()
                            await send(channel, "✅ Recording captured.", file_path=video)

        case "fix":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    await send(channel, STILL_LISTENING)
                    async def fix_status(msg, fpath=None):
                        await send(channel, msg, file_path=fpath)
                    await fix.handle_fix(cmd.raw_cmd or "", ws_key, ws_path, claude,
                                         on_status=fix_status)

        case "widget":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    async def widget_status(msg, fpath=None):
                        await send(channel, msg, file_path=fpath)
                    await widget.handle_widget(cmd.raw_cmd or "", ws_key, ws_path, claude,
                                               on_status=widget_status)

        case "queue":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                elif not cmd.raw_cmd:
                    # Interactive wizard
                    view = QueueBuilderView(message.author.id, channel, ws_key, ws_path)
                    await channel.send(view.build_message(), view=view)
                else:
                    # Inline syntax: /queue task1 --- task2
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
            await send(channel, (
                f"💰 **Daily Spend**\n"
                f"  Today: ${spent:.4f}\n"
                f"  Budget: ${cap:.2f} ({config.QUEUE_STOP_PCT}% cap)\n"
                f"  Remaining: ${remaining:.2f}\n"
                f"  Tasks: {tasks}"
            ))

        case "run":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    await send(channel, await run_cmd.handle_run(cmd.raw_cmd or "", ws_path))

        case "runsh":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                ws_key, ws_path = registry.resolve(None, message.author.id)
                if not ws_path:
                    await send(channel, "❌ No workspace set.")
                else:
                    await send(channel, await run_cmd.handle_runsh(cmd.raw_cmd or "", ws_path))

        # ── Save (game-save-style versioning) ─────────────────────
        case "save":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                match cmd.sub:
                    case "list":
                        text, saves = await git_cmd.handle_save_list(ws_path)
                        if saves and len(saves) > 1:
                            view = SaveListView(message.author.id, ws_path, saves)
                            view.message = await channel.send(text, view=view)
                        else:
                            await send(channel, text)
                    case "undo":
                        await send(channel, await git_cmd.handle_save_undo(ws_path))
                    case "redo":
                        await send(channel, await git_cmd.handle_save_redo(ws_path))
                    case "github":
                        await send(channel, await git_cmd.handle_save_github(ws_path, ws_key))
                    case _:
                        if cmd.raw_cmd:
                            # Custom message: save directly, no preview
                            await send(channel, await git_cmd.handle_save(
                                ws_path, ws_key, claude=claude, custom_msg=cmd.raw_cmd))
                        else:
                            # No message: preview with confirm/edit buttons
                            result = await git_cmd.prepare_save(ws_path, ws_key, claude=claude)
                            if isinstance(result, str):
                                await send(channel, result)
                            else:
                                num, description = result
                                view = SaveConfirmView(ws_path, message.author.id, num, description)
                                preview = (
                                    f"💾 **Save {num}** — {description}\n"
                                    f"-# Click Save to confirm, or edit the description first."
                                )
                                view.message = await channel.send(preview, view=view)

        # ── Git & GitHub ─────────────────────────────────────────
        case "gitstatus":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, await git_cmd.handle_status(ws_path, ws_key))

        case "diff":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                full = cmd.sub == "full" if cmd.sub else False
                await send(channel, await git_cmd.handle_diff(ws_path, full))

        case "commit":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                result = await git_cmd.handle_commit(
                    ws_path, ws_key, message=cmd.raw_cmd, claude=claude, auto_push=True)
                await send(channel, result)

        case "undo":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, await git_cmd.handle_undo(ws_path))

        case "gitlog":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                count = int(cmd.raw_cmd) if cmd.raw_cmd and cmd.raw_cmd.isdigit() else 10
                await send(channel, await git_cmd.handle_log(ws_path, count))

        case "branch":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, await git_cmd.handle_branch(ws_path, cmd.raw_cmd))

        case "stash":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, await git_cmd.handle_stash(ws_path, pop=(cmd.sub == "pop")))

        case "pr":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, await git_cmd.handle_pr(
                    ws_path, ws_key, title=cmd.raw_cmd, claude=claude))

        case "repo":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, await git_cmd.handle_repo(
                    ws_path, ws_key, sub=cmd.sub, arg=cmd.arg))

        case "mirror":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                from commands.scrcpy import handle_mirror
                await send(channel, await handle_mirror(cmd.sub or "start"))

        case "memory":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, memory_cmd.handle_memory(
                    cmd.sub, cmd.arg, ws_path, ws_key))

        case "fixes":
            ws_key, ws_path = registry.resolve(None, message.author.id)
            if not ws_path:
                await send(channel, "❌ No workspace set.")
            else:
                await send(channel, fixes_cmd.handle_fixes(
                    cmd.sub, ws_path, ws_key))

        case "setup":
            import shutil
            checks = []

            # Claude
            claude_path = shutil.which(config.CLAUDE_BIN)
            checks.append(f"{'✅' if claude_path else '❌'} **Claude CLI** — `{claude_path or 'not found'}`")

            # Android
            adb_path = shutil.which(config.ADB_BIN)
            has_avd = bool(config.ANDROID_AVD)
            checks.append(f"{'✅' if adb_path else '❌'} **Android SDK** — adb: `{adb_path or 'not found'}`")
            checks.append(f"{'✅' if has_avd else '⚠️'} **Android AVD** — `{config.ANDROID_AVD or 'not set (set ANDROID_AVD in .env)'}`")

            # iOS
            xcode_path = shutil.which(config.XCODEBUILD)
            checks.append(f"{'✅' if xcode_path else '❌'} **Xcode** — `{xcode_path or 'not found (install from App Store)'}`")
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

            # Web
            checks.append(f"✅ **Web** — port `{config.WEB_SERVE_PORT}`")

            # Tailscale
            if config.TAILSCALE_HOSTNAME:
                checks.append(f"✅ **Tailscale** — `{config.TAILSCALE_HOSTNAME}`")
            else:
                checks.append(f"⚠️ **Tailscale** — not set (optional, for remote access)")

            # Agent mode
            checks.append(f"{'✅' if config.AGENT_MODE else '❌'} **Agent mode** — {'ON' if config.AGENT_MODE else 'OFF (set AGENT_MODE=1 in .env)'}")

            await send(channel, "**Setup Status**\n\n" + "\n".join(checks))

        case "health":
            uptime = int(time.time() - START_TIME)
            m, s = divmod(uptime, 60)
            h, m = divmod(m, 60)
            ws = registry.get_default(message.author.id) or "(none)"
            sess = claude.get_session(ws) or "(none)"
            await send(channel, (
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
            pass  # retired

        case "bot-todo":
            await send(channel, handle_bot_todo(cmd.raw_cmd))

        case "dashboard":
            if not config.AGENT_MODE:
                await send(channel, "🔒 Agent mode OFF.")
            else:
                async def dash_status(msg, fpath=None):
                    await send(channel, msg, file_path=fpath)
                await handle_dashboard(
                    registry, dash_status, rebuild=(cmd.sub == "rebuild"),
                )

        case "newsession":
            ws_key = registry.get_default(message.author.id)
            if ws_key:
                claude.clear_session(ws_key)
                await send(channel, f"🔄 Fresh session for **{ws_key}**.")
            else:
                await send(channel, "❌ No workspace set.")

        case "maintenance":
            if cmd.raw_cmd and cmd.raw_cmd.lower() == "off":
                maintenance_mode = False
                await send(channel, "✅ Maintenance mode **OFF** — public commands are live.")
            else:
                maintenance_mode = True
                if cmd.raw_cmd:
                    maintenance_message = f"🔧 {cmd.raw_cmd}"
                else:
                    maintenance_message = "🔧 Bot is under maintenance — back shortly!"
                await send(channel, f"🔧 Maintenance mode **ON**\nPublic users see: *{maintenance_message}*")

        case "announce":
            if not cmd.raw_cmd:
                await send(channel, "Usage: `/announce <message>`")
            else:
                # Send to announce channel if configured, otherwise just echo in current DM
                target = None
                if config.DISCORD_ANNOUNCE_CHANNEL_ID:
                    target = client.get_channel(config.DISCORD_ANNOUNCE_CHANNEL_ID)
                if target:
                    await target.send(f"📢 {cmd.raw_cmd}")
                    await send(channel, f"✅ Announced in #{target.name}")
                else:
                    await send(channel, f"📢 {cmd.raw_cmd}")

        case "unknown":
            await send(channel, "❓ Unknown command. `/help`")

    # Workspace footer — always show after every command
    await send_workspace_footer(channel, message.author.id, selector_view=_active_selector_view)


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client.run(config.DISCORD_BOT_TOKEN)
