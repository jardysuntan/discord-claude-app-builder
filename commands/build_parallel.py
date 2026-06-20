"""
commands/build_parallel.py — Parallel platform build agents.

/build-parallel runs Android, iOS, and Web builds concurrently, each in its
own Claude Code session. Status updates are posted to per-platform Discord
threads under a parent message. A merge validation step runs after all agents
complete. Falls back to sequential if session limits are hit.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

import config
from agent_protocol import AgentRunner
from agent_loop import run_agent_loop, format_loop_summary, AgentLoopResult
from helpers.budget import BudgetTracker
from workspace_spec import load_workspace_spec, format_spec_context
from platforms import build_platform


PLATFORM_AGENTS = {
    "android": {
        "label": "📱 Android (Compose)",
        "prompt": (
            "The shared KMP module is ready. Ensure the Android target compiles "
            "with Compose UI. Fix any Android-specific issues. "
            "Only modify what's necessary for Android compatibility."
        ),
    },
    "ios": {
        "label": "🍎 iOS (SwiftUI)",
        "prompt": (
            "The shared KMP module is ready. Ensure the iOS target compiles. "
            "Fix any iOS-specific issues. Only modify what's necessary for iOS compatibility. "
            f"IMPORTANT: When running xcodebuild, always use: -destination 'name={config.IOS_SIMULATOR_NAME}'"
        ),
    },
    "web": {
        "label": "🌐 Web (Compose/WASM)",
        "prompt": (
            "The shared KMP module is ready. Ensure the wasmJs web target compiles. "
            "Fix any web-specific issues. Only modify what's necessary for web compatibility."
        ),
    },
}


@dataclass
class ParallelBuildResult:
    """Aggregated result from all parallel platform agents."""
    platform_results: dict[str, AgentLoopResult] = field(default_factory=dict)
    merge_ok: bool = False
    merge_message: str = ""
    total_duration_secs: float = 0.0
    fell_back_to_sequential: bool = False


async def _run_platform_agent(
    platform: str,
    workspace_key: str,
    workspace_path: str,
    claude: AgentRunner,
    on_status: Callable[[str], Awaitable[None]],
    context_prefix: str = "",
    budget: Optional[BudgetTracker] = None,
) -> AgentLoopResult:
    """Run a single platform build agent with status updates."""
    agent_info = PLATFORM_AGENTS[platform]
    await on_status(f"{agent_info['label']} — starting build agent...")

    # Use a platform-specific session key so agents don't collide
    platform_ws_key = f"{workspace_key}-parallel-{platform}"

    result = await run_agent_loop(
        initial_prompt=agent_info["prompt"],
        workspace_key=platform_ws_key,
        workspace_path=workspace_path,
        claude=claude,
        platform=platform,
        on_status=on_status,
        budget=budget,
        context_prefix=context_prefix,
    )

    status = "✅" if result.success else "❌"
    await on_status(f"{agent_info['label']} — {status} {format_loop_summary(result)}")
    return result


async def _validate_merge(
    workspace_path: str,
    platform_results: dict[str, AgentLoopResult],
) -> tuple[bool, str]:
    """Validate shared module compatibility after parallel builds.

    Runs a quick Android build (the primary target) to ensure the shared
    module still compiles after all platform agents have made changes.
    """
    succeeded = [p for p, r in platform_results.items() if r.success]
    failed = [p for p, r in platform_results.items() if not r.success]

    if not succeeded:
        return False, "All platform builds failed — nothing to merge."

    # Quick validation build of the shared module via Android (fastest target)
    result = await build_platform("android", workspace_path)
    if result.success:
        msg = f"✅ Merge validation passed. Shared module is compatible."
        if failed:
            msg += f"\n⚠️ Failed platforms: {', '.join(failed)}"
        return True, msg
    else:
        return False, (
            f"⚠️ Merge validation failed — shared module may have conflicts.\n"
            f"```\n{(result.error or result.output)[:500]}\n```"
        )


async def handle_build_parallel(
    workspace_key: str,
    workspace_path: str,
    claude: AgentRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    create_thread: Optional[Callable[[str], Awaitable[Callable[[str], Awaitable[None]]]]] = None,
    is_admin: bool = True,
    platforms: Optional[list[str]] = None,
) -> ParallelBuildResult:
    """Run parallel platform build agents.

    Args:
        workspace_key: Workspace identifier.
        workspace_path: Path to workspace on disk.
        claude: Agent runner instance.
        on_status: Callback for parent-level status messages (msg, image_path).
        create_thread: Optional factory that creates a Discord thread and returns
                       a status callback for that thread. If None, all status
                       goes through on_status.
        is_admin: Whether the invoking user is admin (gates iOS/Android).
        platforms: Which platforms to build. Defaults to all three.
    """
    start_time = time.time()
    if platforms is None:
        platforms = ["android", "ios", "web"] if is_admin else ["web"]

    # Filter to valid platforms
    platforms = [p for p in platforms if p in PLATFORM_AGENTS]
    if not platforms:
        await on_status("❌ No valid platforms specified.", None)
        return ParallelBuildResult()

    spec = load_workspace_spec(workspace_path)
    context_prefix = format_spec_context(spec) if spec else ""

    await on_status(
        f"🚀 **Parallel build** — launching {len(platforms)} platform agents: "
        f"{', '.join(PLATFORM_AGENTS[p]['label'] for p in platforms)}",
        None,
    )

    # Create per-platform status callbacks via threads (or fallback to parent)
    platform_callbacks: dict[str, Callable[[str], Awaitable[None]]] = {}
    for platform in platforms:
        label = PLATFORM_AGENTS[platform]["label"]
        if create_thread:
            try:
                thread_cb = await create_thread(f"{label} Build")
                platform_callbacks[platform] = thread_cb
            except Exception:
                # Fall back to parent status with platform prefix
                async def _prefixed(msg, _lbl=label):
                    await on_status(f"**{_lbl}** {msg}", None)
                platform_callbacks[platform] = _prefixed
        else:
            async def _prefixed(msg, _lbl=label):
                await on_status(f"**{_lbl}** {msg}", None)
            platform_callbacks[platform] = _prefixed

    # Budget per platform agent
    per_agent_budget = BudgetTracker(
        max_cost_usd=config.MAX_FIX_BUDGET_USD,
        max_invocations=config.MAX_TOTAL_INVOCATIONS,
    )

    # Launch all platform agents concurrently
    result = ParallelBuildResult()
    try:
        tasks = {
            platform: asyncio.create_task(
                _run_platform_agent(
                    platform=platform,
                    workspace_key=workspace_key,
                    workspace_path=workspace_path,
                    claude=claude,
                    on_status=platform_callbacks[platform],
                    context_prefix=context_prefix,
                    budget=per_agent_budget,
                ),
                name=f"build-{platform}",
            )
            for platform in platforms
        }

        # Wait for all with a generous timeout
        done, pending = await asyncio.wait(
            tasks.values(),
            timeout=config.CLAUDE_TIMEOUT * config.MAX_BUILD_ATTEMPTS * len(platforms),
        )

        # Cancel any timed-out tasks
        for task in pending:
            task.cancel()

        # Collect results
        for platform, task in tasks.items():
            if task in done and not task.cancelled():
                try:
                    result.platform_results[platform] = task.result()
                except Exception as exc:
                    result.platform_results[platform] = AgentLoopResult(
                        success=False, total_attempts=0, total_duration_secs=0,
                        final_message=f"❌ Agent crashed: {str(exc)[:200]}",
                    )
            else:
                result.platform_results[platform] = AgentLoopResult(
                    success=False, total_attempts=0, total_duration_secs=0,
                    final_message="❌ Agent timed out.",
                )

    except Exception as exc:
        # Fall back to sequential on any concurrency failure
        await on_status(
            f"⚠️ Parallel execution failed ({str(exc)[:100]}). Falling back to sequential...",
            None,
        )
        result.fell_back_to_sequential = True
        for platform in platforms:
            if platform in result.platform_results:
                continue
            loop_result = await run_agent_loop(
                initial_prompt=PLATFORM_AGENTS[platform]["prompt"],
                workspace_key=workspace_key,
                workspace_path=workspace_path,
                claude=claude,
                platform=platform,
                on_status=platform_callbacks[platform],
                context_prefix=context_prefix,
                budget=per_agent_budget,
            )
            result.platform_results[platform] = loop_result

    # Merge validation step
    await on_status("🔀 Validating shared module compatibility...", None)
    result.merge_ok, result.merge_message = await _validate_merge(
        workspace_path, result.platform_results
    )
    await on_status(result.merge_message, None)

    result.total_duration_secs = time.time() - start_time
    return result


def format_parallel_summary(result: ParallelBuildResult) -> str:
    """Format the final summary for a parallel build."""
    mins = int(result.total_duration_secs // 60)
    secs = int(result.total_duration_secs % 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    lines = [f"**Parallel Build Summary** ({time_str})"]

    if result.fell_back_to_sequential:
        lines.append("⚠️ *Fell back to sequential execution*")

    lines.append("")
    for platform, loop_result in result.platform_results.items():
        label = PLATFORM_AGENTS.get(platform, {}).get("label", platform)
        status = "✅" if loop_result.success else "❌"
        attempts = loop_result.total_attempts
        lines.append(f"  {status} {label}: {attempts} attempt(s)")

    lines.append("")
    lines.append(result.merge_message)

    total_attempts = sum(r.total_attempts for r in result.platform_results.values())
    lines.append(f"\n🔨 Total build attempts: {total_attempts}")

    return "\n".join(lines)
