"""
agent_loop.py — Auto-fix build loop for any platform.
Iterates: build → extract error → Claude fixes → rebuild.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

import config
from claude_runner import ClaudeRunner
from platforms import build_platform, extract_build_error


@dataclass
class BuildAttempt:
    attempt: int
    success: bool
    duration_secs: float
    error_snippet: str = ""
    claude_fix_summary: str = ""


@dataclass
class AgentLoopResult:
    success: bool
    total_attempts: int
    total_duration_secs: float
    attempts: list[BuildAttempt] = field(default_factory=list)
    final_message: str = ""


def _error_is_same(prev: str, new: str) -> bool:
    prev_set = set(prev.strip().splitlines())
    new_set = set(new.strip().splitlines())
    if not prev_set or not new_set:
        return False
    return len(prev_set & new_set) / max(len(prev_set), len(new_set)) > 0.8


async def run_agent_loop(
    initial_prompt: str,
    workspace_key: str,
    workspace_path: str,
    claude: ClaudeRunner,
    platform: str = "android",
    max_attempts: int = None,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> AgentLoopResult:
    """
    1. Send initial prompt to Claude
    2. Build the specified platform
    3. If fail → send error to Claude → rebuild
    4. Repeat until success or max_attempts
    """
    max_attempts = max_attempts or config.MAX_BUILD_ATTEMPTS
    loop_start = time.time()
    attempts: list[BuildAttempt] = []
    last_error = ""

    # Step 1: Initial Claude prompt
    if on_status:
        await on_status("🧠 Sending prompt to Claude...")

    initial_result = await claude.run(
        initial_prompt, workspace_key, workspace_path,
        on_progress=on_status,
    )

    if initial_result.exit_code != 0:
        return AgentLoopResult(
            success=False, total_attempts=0,
            total_duration_secs=time.time() - loop_start,
            final_message=f"Claude failed:\n```\n{initial_result.stderr[:1000]}\n```",
        )

    if on_status:
        preview = initial_result.stdout[:400]
        await on_status(f"✅ Claude responded.\n```\n{preview}\n```")

    # Step 2-4: Build loop
    platform_label = platform.upper() if platform != "all" else "ALL"

    for attempt_num in range(1, max_attempts + 1):
        build_start = time.time()

        if on_status:
            await on_status(f"🔨 [{platform_label}] Build attempt {attempt_num}/{max_attempts}...")

        result = await build_platform(platform, workspace_path)
        build_duration = time.time() - build_start

        if result.success:
            attempts.append(BuildAttempt(attempt=attempt_num, success=True, duration_secs=build_duration))
            return AgentLoopResult(
                success=True, total_attempts=attempt_num,
                total_duration_secs=time.time() - loop_start,
                attempts=attempts,
                final_message=f"✅ {platform_label} build succeeded on attempt {attempt_num}.",
            )

        error_snippet = result.error or extract_build_error(result.output)

        if last_error and _error_is_same(last_error, error_snippet):
            attempts.append(BuildAttempt(
                attempt=attempt_num, success=False,
                duration_secs=build_duration, error_snippet=error_snippet[:500],
            ))
            return AgentLoopResult(
                success=False, total_attempts=attempt_num,
                total_duration_secs=time.time() - loop_start,
                attempts=attempts,
                final_message=(
                    f"🛑 Stopping — same error repeating on attempt {attempt_num}.\n\n"
                    f"**Error:**\n```\n{error_snippet[:800]}\n```"
                ),
            )

        last_error = error_snippet

        if on_status:
            await on_status(
                f"⚠️ Attempt {attempt_num} failed. Sending error to Claude...\n"
                f"```\n{error_snippet[:300]}\n```"
            )

        fix_prompt = (
            f"The {platform} build failed. Fix the code so it compiles.\n"
            f"Only modify what's necessary.\n\n```\n{error_snippet}\n```"
        )
        fix_result = await claude.run(
            fix_prompt, workspace_key, workspace_path,
            on_progress=on_status,
        )

        attempts.append(BuildAttempt(
            attempt=attempt_num, success=False,
            duration_secs=build_duration,
            error_snippet=error_snippet[:500],
            claude_fix_summary=(fix_result.stdout[:300] if fix_result.stdout else ""),
        ))

        if fix_result.exit_code != 0:
            return AgentLoopResult(
                success=False, total_attempts=attempt_num,
                total_duration_secs=time.time() - loop_start,
                attempts=attempts,
                final_message=f"🛑 Claude errored on fix:\n```\n{fix_result.stderr[:500]}\n```",
            )

    return AgentLoopResult(
        success=False, total_attempts=max_attempts,
        total_duration_secs=time.time() - loop_start,
        attempts=attempts,
        final_message=f"🛑 Build failed after {max_attempts} attempts.\n```\n{last_error[:800]}\n```",
    )


def format_loop_summary(result: AgentLoopResult) -> str:
    mins = int(result.total_duration_secs // 60)
    secs = int(result.total_duration_secs % 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    lines = [result.final_message]
    if result.total_attempts > 1:
        lines.append(f"\n**Build loop** ({time_str}, {result.total_attempts} attempts):")
        for a in result.attempts:
            status = "✅" if a.success else "❌"
            lines.append(f"  {status} Attempt {a.attempt} ({a.duration_secs:.0f}s)")
    return "\n".join(lines)
