"""commands/fix.py — /fix for auto-fix build loop (Android + Web)."""

from claude_runner import ClaudeRunner
from agent_loop import run_agent_loop, format_loop_summary
from typing import Callable, Awaitable, Optional


async def handle_fix(instructions, workspace_key, workspace_path, claude,
                     platform="android",
                     on_status: Callable[[str, Optional[str]], Awaitable[None]] = None):
    if not instructions:
        prompt = (
            f"Review the {platform} target of this project. "
            "Fix any issues preventing compilation. "
            "Make sure the app fully compiles and builds."
        )
    else:
        prompt = (
            f"{instructions}\n\n"
            f"After making changes, ensure the {platform} target compiles. "
            "Fix any errors."
        )

    async def loop_status(msg):
        await on_status(msg, None)

    result = await run_agent_loop(
        initial_prompt=prompt, workspace_key=workspace_key,
        workspace_path=workspace_path, claude=claude,
        platform=platform, on_status=loop_status,
    )
    await on_status(format_loop_summary(result), None)

    if not result.success:
        return

    # Also ensure web target compiles (iterate until it passes)
    if platform != "web":
        await on_status("🌐 **Web** — checking browser target...", None)
        web_result = await run_agent_loop(
            initial_prompt=(
                f"The {platform} target compiles. Now ensure the wasmJs web target "
                "also compiles. Fix any web-specific issues. "
                "Only modify what's necessary for web compatibility."
            ),
            workspace_key=workspace_key,
            workspace_path=workspace_path,
            claude=claude,
            platform="web",
            on_status=loop_status,
        )
        web_summary = format_loop_summary(web_result)
        await on_status(web_summary, None)

        if not web_result.success:
            await on_status(
                f"⚠️ Web build had issues (Android works fine).\n"
                f"Use `@{workspace_key} Fix the wasmJs web target` to resolve.",
                None,
            )

    await on_status(
        f"📱 Android: ✅  |  🌐 Web: {'✅' if platform == 'web' or web_result.success else '❌'}\n"
        "Use `/demo android|ios|web` to see the result.",
        None,
    )
