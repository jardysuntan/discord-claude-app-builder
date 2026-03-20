"""
helpers/demo_runner.py — run a demo for a single platform (ios, android, web).

Extracted from bot.py `_run_demo()`.  Accepts a BotContext so all sends go
through ctx.send() instead of a module-level send().
"""

import asyncio

import config
from agent_loop import run_agent_loop, format_loop_summary
from bot_context import STILL_LISTENING
from commands import fixes_cmd
from helpers.budget import BudgetTracker
from platforms import (
    AndroidPlatform,
    iOSPlatform,
    WebPlatform,
    demo_platform,
)
from helpers.web_screenshot import take_web_screenshot


async def run_demo(ctx, channel, ws_key: str, ws_path: str, platform: str,
                   budget: BudgetTracker = None):
    """Run a demo for a single platform. Shared by /demo <plat> and DemoPlatformView."""
    if budget is None:
        budget = BudgetTracker(
            max_cost_usd=config.MAX_FIX_BUDGET_USD,
            max_invocations=config.MAX_TOTAL_INVOCATIONS,
        )
    await ctx.send(channel, f"📱 Demoing **{ws_key}** [{platform}]...")
    await ctx.send(channel, STILL_LISTENING)

    if platform == "ios":
        await ctx.send(channel, "Booting iOS Simulator...")
        ok, sim_msg = await iOSPlatform.ensure_simulator()
        if not ok:
            await ctx.send(channel, f"❌ {sim_msg}")
        else:
            await ctx.send(channel, f"{sim_msg} Building KMP framework + Xcode project...")
            build_result = await iOSPlatform.build(ws_path)

            # Auto-fix: if build fails, use agent loop (same as /buildapp iOS)
            if not build_result.success:
                await ctx.send(channel, "⚠️ iOS build failed — auto-fixing...")

                async def ios_fix_status(msg):
                    await ctx.send(channel, msg)

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
                    claude=ctx.claude,
                    platform="ios",
                    max_attempts=config.MAX_BUILD_ATTEMPTS,
                    on_status=ios_fix_status,
                    budget=budget,
                )
                if not fix_result.success:
                    summary = format_loop_summary(fix_result)
                    await ctx.send(channel, summary)
                    build_result = None
                else:
                    await ctx.send(channel, "✅ iOS build fixed!")
                    try:
                        fixes_cmd.log_fix(ws_path, "ios", build_result.error[:300] if build_result.error else "Build error",
                                          "Auto-fixed iOS build failure")
                    except Exception:
                        pass

            if build_result is None:
                pass  # auto-fix failed, already reported
            else:
                await ctx.send(channel, "Build succeeded. Installing on simulator...")
                bundle_id = await iOSPlatform.install_and_launch(ws_path)
                if bundle_id.startswith(("Could not", "Install failed", "Installed but")):
                    await ctx.send(channel, f"❌ {bundle_id}")
                else:
                    await ctx.send(channel, f"Launched **{bundle_id}**. Checking for crashes...")
                    await asyncio.sleep(3)

                    # Check for runtime crash
                    crash_log = await iOSPlatform.check_crash(bundle_id)
                    if crash_log:
                        if budget.exceeded:
                            await ctx.send(channel, budget.exceeded_message)
                            return
                        await ctx.send(channel, "💥 App crashed on launch — auto-fixing...")
                        async def crash_fix_status(msg):
                            await ctx.send(channel, msg)

                        crash_fixed = False
                        for crash_attempt in range(1, config.MAX_BUILD_ATTEMPTS + 1):
                            if budget.exceeded:
                                await ctx.send(channel, budget.exceeded_message)
                                return

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
                                claude=ctx.claude,
                                platform="ios",
                                max_attempts=config.MAX_BUILD_ATTEMPTS,
                                on_status=crash_fix_status,
                                budget=budget,
                            )
                            if not fix_result.success:
                                await ctx.send(channel, format_loop_summary(fix_result))
                                break

                            # Rebuild succeeded — try launching again
                            bundle_id = await iOSPlatform.install_and_launch(ws_path)
                            if bundle_id.startswith(("Could not", "Install failed", "Installed but")):
                                await ctx.send(channel, f"❌ {bundle_id}")
                                break

                            await asyncio.sleep(3)
                            crash_log = await iOSPlatform.check_crash(bundle_id)
                            if not crash_log:
                                crash_fixed = True
                                break
                            await ctx.send(channel, f"💥 Still crashing (attempt {crash_attempt})— retrying fix...")

                        if crash_fixed:
                            await ctx.send(channel, "✅ Crash fixed!")
                            try:
                                fixes_cmd.log_fix(ws_path, "ios", f"Runtime crash: {crash_log[:300]}",
                                                  "Fixed crash-on-launch")
                            except Exception:
                                pass
                        else:
                            if not crash_log:
                                pass  # already reported above
                            else:
                                await ctx.send(channel, f"❌ App still crashing after {config.MAX_BUILD_ATTEMPTS} fix attempts.")
                            return

                    # App is running — take screenshot
                    screenshot = await iOSPlatform.screenshot()
                    await ctx.send(channel, f"✅ **{bundle_id}** running on iOS Simulator.", file_path=screenshot)
    elif platform == "android":
        await ctx.send(channel, "Checking Android device/emulator...")
        ok, dev_msg = await AndroidPlatform.ensure_device()
        if not ok:
            await ctx.send(channel, f"❌ {dev_msg}")
        else:
            await ctx.send(channel, f"{dev_msg} Building Android APK...")
            build_result = await AndroidPlatform.build(ws_path)

            # Auto-fix: if build fails, use agent loop
            if not build_result.success:
                await ctx.send(channel, "⚠️ Android build failed — auto-fixing...")

                async def android_fix_status(msg):
                    await ctx.send(channel, msg)

                fix_result = await run_agent_loop(
                    initial_prompt=(
                        "The Android build failed. Fix the code so it compiles for Android.\n"
                        "Only modify what's necessary for Android compatibility.\n\n"
                        f"```\n{build_result.error[:800]}\n```"
                    ),
                    workspace_key=ws_key,
                    workspace_path=ws_path,
                    claude=ctx.claude,
                    platform="android",
                    max_attempts=config.MAX_BUILD_ATTEMPTS,
                    on_status=android_fix_status,
                    budget=budget,
                )
                if not fix_result.success:
                    summary = format_loop_summary(fix_result)
                    await ctx.send(channel, summary)
                    build_result = None
                else:
                    await ctx.send(channel, "✅ Android build fixed!")
                    try:
                        fixes_cmd.log_fix(ws_path, "android", build_result.error[:300] if build_result.error else "Build error",
                                          "Auto-fixed Android build failure")
                    except Exception:
                        pass

            if build_result is None:
                pass  # auto-fix failed, already reported
            else:
                await ctx.send(channel, "Build succeeded. Installing on device...")
                install_result = await AndroidPlatform.install(ws_path)
                if not install_result.success:
                    await ctx.send(channel, f"❌ Install failed:\n```\n{install_result.error[:800]}\n```")
                else:
                    await AndroidPlatform.clear_logcat()
                    app_id = await AndroidPlatform.launch(ws_path)
                    if app_id.startswith("Could not"):
                        await ctx.send(channel, f"❌ {app_id}")
                    else:
                        await ctx.send(channel, f"Launched **{app_id}**. Checking for crashes...")
                        await asyncio.sleep(3)

                        crash_log = await AndroidPlatform.check_crash(app_id)
                        if crash_log:
                            if budget.exceeded:
                                await ctx.send(channel, budget.exceeded_message)
                                return
                            await ctx.send(channel, "💥 App crashed on launch — auto-fixing...")
                            async def android_crash_fix_status(msg):
                                await ctx.send(channel, msg)

                            crash_fixed = False
                            for crash_attempt in range(1, config.MAX_BUILD_ATTEMPTS + 1):
                                if budget.exceeded:
                                    await ctx.send(channel, budget.exceeded_message)
                                    return

                                fix_result = await run_agent_loop(
                                    initial_prompt=(
                                        f"The Android app ({app_id}) crashes on launch with a runtime error.\n"
                                        "Fix the code so it runs without crashing.\n\n"
                                        f"Crash log (from logcat):\n```\n{crash_log[:800]}\n```"
                                    ),
                                    workspace_key=ws_key,
                                    workspace_path=ws_path,
                                    claude=ctx.claude,
                                    platform="android",
                                    max_attempts=config.MAX_BUILD_ATTEMPTS,
                                    on_status=android_crash_fix_status,
                                    budget=budget,
                                )
                                if not fix_result.success:
                                    await ctx.send(channel, format_loop_summary(fix_result))
                                    break

                                # Rebuild + reinstall + relaunch
                                install_result = await AndroidPlatform.install(ws_path)
                                if not install_result.success:
                                    await ctx.send(channel, f"❌ Reinstall failed:\n```\n{install_result.error[:800]}\n```")
                                    break

                                await AndroidPlatform.clear_logcat()
                                app_id = await AndroidPlatform.launch(ws_path)
                                if app_id.startswith("Could not"):
                                    await ctx.send(channel, f"❌ {app_id}")
                                    break

                                await asyncio.sleep(3)
                                crash_log = await AndroidPlatform.check_crash(app_id)
                                if not crash_log:
                                    crash_fixed = True
                                    break
                                await ctx.send(channel, f"💥 Still crashing (attempt {crash_attempt}) — retrying fix...")

                            if crash_fixed:
                                await ctx.send(channel, "✅ Crash fixed!")
                                try:
                                    fixes_cmd.log_fix(ws_path, "android", f"Runtime crash: {crash_log[:300]}",
                                                      "Fixed crash-on-launch")
                                except Exception:
                                    pass
                            else:
                                if not crash_log:
                                    pass
                                else:
                                    await ctx.send(channel, f"❌ App still crashing after {config.MAX_BUILD_ATTEMPTS} fix attempts.")
                                return

                        # App is running — take screenshot
                        screenshot = await AndroidPlatform.screenshot()
                        await ctx.send(channel, f"✅ **{app_id}** running on Android.", file_path=screenshot)

    elif platform == "web":
        await ctx.send(channel, "Building web app...")
        build_result = await WebPlatform.build(ws_path)

        # Auto-fix: if build fails, use agent loop
        if not build_result.success:
            await ctx.send(channel, "⚠️ Web build failed — auto-fixing...")

            async def web_fix_status(msg):
                await ctx.send(channel, msg)

            fix_result = await run_agent_loop(
                initial_prompt=(
                    "The Web (WASM/JS) build failed. Fix the code so it compiles for web.\n"
                    "Only modify what's necessary for web compatibility.\n\n"
                    f"```\n{build_result.error[:800]}\n```"
                ),
                workspace_key=ws_key,
                workspace_path=ws_path,
                claude=ctx.claude,
                platform="web",
                max_attempts=config.MAX_BUILD_ATTEMPTS,
                on_status=web_fix_status,
                budget=budget,
            )
            if not fix_result.success:
                summary = format_loop_summary(fix_result)
                await ctx.send(channel, summary)
                build_result = None
            else:
                await ctx.send(channel, "✅ Web build fixed!")
                try:
                    fixes_cmd.log_fix(ws_path, "web", build_result.error[:300] if build_result.error else "Build error",
                                      "Auto-fixed Web build failure")
                except Exception:
                    pass

        if build_result is None:
            pass  # auto-fix failed, already reported
        else:
            await ctx.send(channel, "Build succeeded. Starting web server...")
            url = await WebPlatform.serve(ws_path, ws_key)
            if not url:
                await ctx.send(channel, "❌ Built but could not find distribution directory.")
            else:
                await asyncio.sleep(2)
                # Health check always uses localhost (server-side check)
                local_url = f"http://localhost:{config.WEB_SERVE_PORT}"
                health_err = await WebPlatform.check_health(local_url)
                if health_err:
                    if budget.exceeded:
                        await ctx.send(channel, budget.exceeded_message)
                        return
                    await ctx.send(channel, f"⚠️ Web app unhealthy ({health_err}) — auto-fixing...")
                    async def web_health_fix_status(msg):
                        await ctx.send(channel, msg)

                    health_fixed = False
                    for health_attempt in range(1, config.MAX_BUILD_ATTEMPTS + 1):
                        if budget.exceeded:
                            await ctx.send(channel, budget.exceeded_message)
                            return

                        fix_result = await run_agent_loop(
                            initial_prompt=(
                                f"The web app built and is being served at {url}, but the health check failed.\n"
                                "Fix the code so the web app loads correctly in a browser.\n\n"
                                f"Health check error:\n```\n{health_err}\n```"
                            ),
                            workspace_key=ws_key,
                            workspace_path=ws_path,
                            claude=ctx.claude,
                            platform="web",
                            max_attempts=config.MAX_BUILD_ATTEMPTS,
                            on_status=web_health_fix_status,
                            budget=budget,
                        )
                        if not fix_result.success:
                            await ctx.send(channel, format_loop_summary(fix_result))
                            break

                        # Rebuild + re-serve + re-check
                        rebuild = await WebPlatform.build(ws_path)
                        if not rebuild.success:
                            await ctx.send(channel, f"❌ Rebuild failed:\n```\n{rebuild.error[:800]}\n```")
                            break

                        url = await WebPlatform.serve(ws_path, ws_key)
                        if not url:
                            await ctx.send(channel, "❌ Could not find distribution directory after rebuild.")
                            break

                        await asyncio.sleep(2)
                        health_err = await WebPlatform.check_health(local_url)
                        if not health_err:
                            health_fixed = True
                            break
                        await ctx.send(channel, f"⚠️ Still unhealthy (attempt {health_attempt}) — retrying fix...")

                    if health_fixed:
                        await ctx.send(channel, "✅ Web app healthy!")
                        try:
                            fixes_cmd.log_fix(ws_path, "web", f"Health check: {health_err or 'failed'}",
                                              "Fixed web health check")
                        except Exception:
                            pass
                    else:
                        if not health_err:
                            pass
                        else:
                            await ctx.send(channel, f"❌ Web app still unhealthy after {config.MAX_BUILD_ATTEMPTS} fix attempts.")
                        return

                await ctx.send(channel, f"✅ Web app live!\n🔗 {url}")
                shot = await take_web_screenshot(f"http://localhost:{config.WEB_SERVE_PORT}")
                if shot:
                    await ctx.send(channel, "📸 Preview:", file_path=shot)

    else:
        result = await demo_platform(platform, ws_path)
        msg = result.message
        if result.demo_url:
            msg += f"\n🔗 {result.demo_url}"
        await ctx.send(channel, msg, file_path=result.screenshot_path)
