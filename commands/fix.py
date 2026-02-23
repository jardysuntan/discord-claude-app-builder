"""commands/fix.py — /fix for auto-fix build loop."""

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
    if result.success:
        await on_status("Use `/demo android|ios|web` to see the result.", None)
