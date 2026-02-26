"""
commands/buildapp.py — One-message "idea to running app" for KMP.

/buildapp <description>
  → scaffold KMP project → Claude builds features → auto-fix → demo all platforms
"""

import re
import time
from typing import Callable, Awaitable, Optional

import config
from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner
from agent_loop import run_agent_loop, format_loop_summary
from commands.create import create_kmp_project
from platforms import AndroidPlatform, iOSPlatform, WebPlatform


def infer_app_name(description: str) -> str:
    fillers = {"a", "an", "the", "with", "and", "for", "that", "this", "my",
               "app", "application", "make", "create", "build"}
    words = description.split()
    meaningful = [w for w in words if w.lower() not in fillers and len(w) > 2]
    name_words = meaningful[:3] if len(meaningful) >= 2 else words[:2]
    return "".join(w.capitalize() for w in name_words if w.isalpha()) or "MyApp"


def build_feature_prompt(app_name: str, description: str) -> str:
    return f"""Build a complete Kotlin Multiplatform app called "{app_name}".

Description: {description}

This is a Compose Multiplatform project. Write ALL shared UI code in
composeApp/src/commonMain/ using Compose Multiplatform APIs.

Requirements:
- Material 3 components and theming
- Clean, polished UI that looks great on first launch
- All shared logic and UI in commonMain
- Platform-specific code only where absolutely necessary (use expect/actual)
- Make sure ALL imports exist and ALL dependencies are in build.gradle.kts
- Verify the code compiles for Android target first
- IMPORTANT: Do NOT use emoji characters (Unicode emoji) in the UI. They render as broken boxes on the Web (WASM) target. Use Material Icons from `androidx.compose.material.icons.Icons` instead.

Write complete, working code. No TODOs or placeholders."""


async def handle_buildapp(
    description: str,
    registry: WorkspaceRegistry,
    claude: ClaudeRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
) -> None:
    if not description:
        await on_status("Usage: `/buildapp <description of the app>`", None)
        return

    start_time = time.time()
    app_name = infer_app_name(description)

    # 1. Scaffold
    await on_status(f"🏗️ Creating **{app_name}** (Kotlin Multiplatform)...", None)
    await on_status("💡 *I'm still listening — feel free to send other commands while this runs.*", None)
    scaffold_result = await create_kmp_project(app_name, registry)
    await on_status(scaffold_result.message, None)

    if not scaffold_result.success:
        return

    slug = scaffold_result.slug
    app_name = slug  # use actual name (may have been incremented)
    ws_path = registry.get_path(slug)
    if not ws_path:
        await on_status(f"❌ Could not find workspace `{slug}`.", None)
        return

    # 2. Claude builds features + auto-fix for Android first
    feature_prompt = build_feature_prompt(app_name, description)

    async def loop_status(msg):
        await on_status(msg, None)

    loop_result = await run_agent_loop(
        initial_prompt=feature_prompt,
        workspace_key=slug,
        workspace_path=ws_path,
        claude=claude,
        platform="android",
        on_status=loop_status,
    )

    summary = format_loop_summary(loop_result)
    await on_status(summary, None)

    if not loop_result.success:
        await on_status(
            f"Android build didn't succeed. Try `@{slug} <fix instructions>`.",
            None,
        )
        return

    # 3. Android demo
    await on_status("📱 **Android** — launching demo...", None)
    android_demo = await AndroidPlatform.full_demo(ws_path)
    await on_status(android_demo.message, android_demo.screenshot_path)

    # 4. Web build + auto-fix (so anyone can try it in browser)
    await on_status("🌐 **Web** — building and fixing browser version...", None)
    web_loop = await run_agent_loop(
        initial_prompt=(
            "The Android target compiles. Now ensure the wasmJs web target "
            "also compiles. Fix any web-specific issues. "
            "Only modify what's necessary for web compatibility."
        ),
        workspace_key=slug,
        workspace_path=ws_path,
        claude=claude,
        platform="web",
        on_status=loop_status,
    )
    web_summary = format_loop_summary(web_loop)
    await on_status(web_summary, None)

    web_demo_url = None
    if web_loop.success:
        url = await WebPlatform.serve(ws_path)
        if url:
            web_demo_url = url
            await on_status(
                f"✅ Web version live → {url}\n"
                f"Anyone can try it in their browser!",
                None,
            )
        else:
            await on_status("✅ Web builds but couldn't start server.", None)
    else:
        await on_status(
            f"⚠️ Web build had issues (Android version works fine).\n"
            f"Use `@{slug} Fix the wasmJs web target` to resolve.",
            None,
        )

    # 5. iOS build + auto-fix (same as web)
    await on_status("🍎 **iOS** — building and fixing simulator version...", None)
    ios_loop = await run_agent_loop(
        initial_prompt=(
            "The Android target compiles. Now ensure the iOS target "
            "also compiles. Fix any iOS-specific issues. "
            "Only modify what's necessary for iOS compatibility. "
            f"IMPORTANT: When running xcodebuild, always use: -destination 'name={config.IOS_SIMULATOR_NAME}'"
        ),
        workspace_key=slug,
        workspace_path=ws_path,
        claude=claude,
        platform="ios",
        on_status=loop_status,
    )
    ios_loop_summary = format_loop_summary(ios_loop)
    await on_status(ios_loop_summary, None)

    ios_demo = None
    if ios_loop.success:
        await on_status("📱 Launching iOS demo...", None)
        ios_demo = await iOSPlatform.full_demo(ws_path)
        if ios_demo.success:
            await on_status(ios_demo.message, ios_demo.screenshot_path)
        else:
            await on_status("✅ iOS builds but demo failed.", None)
    else:
        await on_status(
            f"⚠️ iOS build had issues. Use `@{slug} Fix the iOS target` to resolve.",
            None,
        )

    # 6. Final summary
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    build_attempts = loop_result.total_attempts + web_loop.total_attempts + ios_loop.total_attempts

    platform_status = []
    platform_status.append(f"  📱 Android: {'✅' if loop_result.success else '❌'}")
    platform_status.append(f"  🌐 Web: {'✅ ' + (web_demo_url or '') if web_loop.success else '❌'}")
    ios_ok = ios_demo.success if ios_demo else False
    platform_status.append(f"  🍎 iOS: {'✅' if ios_ok else '❌'}")

    await on_status(
        f"🎉 **{app_name}** built!\n\n"
        f"  ⏱️ Total: {mins}m {secs}s\n"
        f"  🔨 Build attempts: {build_attempts}\n\n"
        + "\n".join(platform_status) + "\n\n"
        f"Commands:\n"
        f"  `@{slug} <prompt>` — add features\n"
        f"  `/demo android|ios|web` — see it running\n"
        f"  `/build android|ios|web` — rebuild a target\n"
        f"  `/fix` — auto-fix build errors",
        None,
    )
