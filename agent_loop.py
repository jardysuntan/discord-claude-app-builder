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
from commands.fixes_cmd import log_fix, get_recent_fixes
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

    # Step 1: Initial Claude prompt (with retry)
    max_claude_retries = 2
    initial_result = None

    for claude_try in range(1, max_claude_retries + 1):
        if on_status:
            label = "🧠 Sending prompt to Claude..."
            if claude_try > 1:
                label = f"🔄 Retrying Claude (attempt {claude_try}/{max_claude_retries})..."
            await on_status(label)

        initial_result = await claude.run(
            initial_prompt, workspace_key, workspace_path,
            on_progress=on_status,
        )

        if initial_result.exit_code == 0:
            break

        # On failure, clear session and retry
        error_detail = initial_result.stderr.strip() or initial_result.stdout.strip() or "No error details"
        if on_status and claude_try < max_claude_retries:
            await on_status(f"⚠️ Claude failed (attempt {claude_try}): {error_detail[:200]}\nRetrying...")
        claude.clear_session(workspace_key)
        await asyncio.sleep(2)

    if initial_result.exit_code != 0:
        error_detail = initial_result.stderr.strip() or initial_result.stdout.strip() or "Unknown error (no output)"
        return AgentLoopResult(
            success=False, total_attempts=0,
            total_duration_secs=time.time() - loop_start,
            final_message=f"Claude failed after {max_claude_retries} attempts:\n```\n{error_detail[:1000]}\n```",
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

        sim_hint = ""
        if platform == "ios":
            sim_hint = (
                f"IMPORTANT: When running xcodebuild, always use: -destination 'name={config.IOS_SIMULATOR_NAME}'\n"
                "NEVER use 'simctl launch --console' — it blocks forever. Use 'simctl launch' without --console.\n"
            )

        fixes_context = get_recent_fixes(workspace_path)
        past_fixes = f"Previous fixes for this project:\n{fixes_context}\n\n" if fixes_context else ""

        fix_prompt = (
            f"{past_fixes}"
            f"The {platform} build failed. Fix the code so it compiles.\n"
            f"Only modify what's necessary.\n{sim_hint}\n```\n{error_snippet}\n```"
        )

        # Try fix with retry on Claude failure
        fix_result = None
        for fix_try in range(1, 3):
            fix_result = await claude.run(
                fix_prompt, workspace_key, workspace_path,
                on_progress=on_status,
            )
            if fix_result.exit_code == 0:
                break
            claude.clear_session(workspace_key)
            if fix_try < 2 and on_status:
                await on_status("⚠️ Claude failed on fix, retrying...")
            await asyncio.sleep(2)

        attempts.append(BuildAttempt(
            attempt=attempt_num, success=False,
            duration_secs=build_duration,
            error_snippet=error_snippet[:500],
            claude_fix_summary=(fix_result.stdout[:300] if fix_result.stdout else ""),
        ))

        if fix_result.exit_code == 0:
            try:
                log_fix(workspace_path, platform, error_snippet[:300],
                        fix_result.stdout[:300] if fix_result.stdout else "Applied fix")
            except Exception:
                pass  # don't break the build loop over logging

        if fix_result.exit_code != 0:
            error_detail = fix_result.stderr.strip() or fix_result.stdout.strip() or "Unknown error"
            return AgentLoopResult(
                success=False, total_attempts=attempt_num,
                total_duration_secs=time.time() - loop_start,
                attempts=attempts,
                final_message=f"🛑 Claude errored on fix:\n```\n{error_detail[:500]}\n```",
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
