"""
helpers/smoketest_runner.py — Core smoke-test logic.

Runs a full buildapp → demo cycle with a deterministic prompt,
validates every stage, records results, and cleans up on pass.

Can be called from:
  - /smoketest slash command (manual trigger)
  - A cron/scheduled job (pass channel_id to post results to)
"""

import shutil
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

import config
from agent_loop import run_agent_loop, AgentLoopResult
from commands.buildapp import infer_app_name, build_feature_prompt
from commands.create import create_kmp_project
from helpers.budget import BudgetTracker
from platforms import WebPlatform
from helpers.web_screenshot import take_web_screenshot
from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner

# ── Constants ────────────────────────────────────────────────────────────────

SMOKE_PROMPT = (
    "a counter app with increment and decrement buttons and a reset button"
)

SMOKE_APP_NAME = "SmokeTest"


@dataclass
class StageResult:
    name: str
    passed: bool
    duration_secs: float = 0.0
    detail: str = ""


@dataclass
class SmokeTestResult:
    success: bool = False
    stages: list[StageResult] = field(default_factory=list)
    workspace_slug: str = ""
    workspace_path: str = ""
    total_duration_secs: float = 0.0
    fix_loop_count: int = 0
    total_cost_usd: float = 0.0

    def summary(self) -> str:
        """Human-readable summary suitable for Discord."""
        mins = int(self.total_duration_secs // 60)
        secs = int(self.total_duration_secs % 60)

        lines = []
        if self.success:
            lines.append("✅ **Smoke Test: PASS**")
        else:
            lines.append("🚨 **Smoke Test: FAIL** 🚨")
        lines.append(f"  Total time: {mins}m {secs}s")
        lines.append(f"  Fix loops: {self.fix_loop_count}")
        lines.append(f"  Cost: ${self.total_cost_usd:.2f}")
        lines.append("")

        for stage in self.stages:
            icon = "\u2705" if stage.passed else "\u274c"
            dur = f" ({stage.duration_secs:.0f}s)" if stage.duration_secs else ""
            detail = f" \u2014 {stage.detail}" if stage.detail else ""
            lines.append(f"  {icon} {stage.name}{dur}{detail}")

        if not self.success:
            # Find first failure
            for stage in self.stages:
                if not stage.passed:
                    lines.append(f"\n**Broke at:** {stage.name}")
                    if stage.detail:
                        lines.append(f"```\n{stage.detail[:500]}\n```")
                    break

        return "\n".join(lines)


async def run_smoketest(
    registry: WorkspaceRegistry,
    claude: ClaudeRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    is_admin: bool = False,
    owner_id: Optional[int] = None,
) -> SmokeTestResult:
    """Run the full smoke test. Returns a SmokeTestResult."""

    result = SmokeTestResult()
    overall_start = time.time()
    budget = BudgetTracker(
        max_cost_usd=config.MAX_FIX_BUDGET_USD,
        max_invocations=config.MAX_TOTAL_INVOCATIONS,
    )

    async def status(msg, file_path=None):
        await on_status(msg, file_path)

    # ── Stage 1: Create workspace ────────────────────────────────────────
    await status("🧪 **Smoke test** starting...", None)

    t0 = time.time()
    scaffold = await create_kmp_project(SMOKE_APP_NAME, registry, owner_id=owner_id)
    dur = time.time() - t0

    if not scaffold.success or not scaffold.slug:
        result.stages.append(StageResult(
            "Create workspace", False, dur, scaffold.message,
        ))
        result.total_duration_secs = time.time() - overall_start
        return result

    result.stages.append(StageResult("Create workspace", True, dur))
    slug = scaffold.slug
    result.workspace_slug = slug
    ws_path = registry.get_path(slug)
    result.workspace_path = ws_path or ""

    if not ws_path:
        result.stages.append(StageResult(
            "Resolve workspace", False, 0, f"Path not found for {slug}",
        ))
        result.total_duration_secs = time.time() - overall_start
        return result

    await status(f"Created workspace **{slug}**", None)

    # ── Stage 2: Claude generates code (Android) ─────────────────────────
    feature_prompt = build_feature_prompt(SMOKE_APP_NAME, SMOKE_PROMPT)

    async def loop_status(msg):
        await status(msg, None)

    t0 = time.time()
    loop_result: AgentLoopResult = await run_agent_loop(
        initial_prompt=feature_prompt,
        workspace_key=slug,
        workspace_path=ws_path,
        claude=claude,
        platform="android",
        on_status=loop_status,
        budget=budget,
    )
    dur = time.time() - t0
    result.fix_loop_count += max(loop_result.total_attempts - 1, 0)
    result.total_cost_usd = budget.total_cost_usd

    # Claude response stage
    claude_ok = loop_result.total_attempts > 0 or loop_result.success
    result.stages.append(StageResult(
        "Claude response", claude_ok, 0,
        "" if claude_ok else loop_result.final_message[:200],
    ))
    if not claude_ok:
        result.total_duration_secs = time.time() - overall_start
        _schedule_cleanup(result, registry, slug, ws_path, passed=False)
        return result

    # Code generated stage
    result.stages.append(StageResult("Code generated", True, 0))

    # Build passes stage
    result.stages.append(StageResult(
        "Android build", loop_result.success, dur,
        "" if loop_result.success else loop_result.final_message[:200],
    ))
    if not loop_result.success:
        result.total_duration_secs = time.time() - overall_start
        _schedule_cleanup(result, registry, slug, ws_path, passed=False)
        return result

    await status("Android build passed", None)

    # ── Stage 3: Web build + demo ────────────────────────────────────────
    t0 = time.time()
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
        budget=budget,
    )
    dur = time.time() - t0
    result.fix_loop_count += max(web_loop.total_attempts - 1, 0)
    result.total_cost_usd = budget.total_cost_usd

    result.stages.append(StageResult(
        "Web build", web_loop.success, dur,
        "" if web_loop.success else web_loop.final_message[:200],
    ))
    if not web_loop.success:
        result.total_duration_secs = time.time() - overall_start
        _schedule_cleanup(result, registry, slug, ws_path, passed=False)
        return result

    await status("Web build passed", None)

    # ── Stage 4: Screenshot ──────────────────────────────────────────────
    t0 = time.time()
    screenshot_path: Optional[str] = None
    url = await WebPlatform.serve(ws_path, workspace_key=slug)
    if url:
        import asyncio
        await asyncio.sleep(2)
        screenshot_path = await take_web_screenshot(
            f"http://localhost:{config.WEB_SERVE_PORT}"
        )
    dur = time.time() - t0

    screenshot_ok = screenshot_path is not None
    result.stages.append(StageResult(
        "Screenshot captured", screenshot_ok, dur,
        "" if screenshot_ok else "Web server or screenshot failed",
    ))

    if screenshot_ok:
        await status("Screenshot captured", screenshot_path)

    # ── Optional: iOS / Android demos (admin only, best-effort) ──────────
    if is_admin:
        # Android demo
        from platforms import AndroidPlatform
        t0 = time.time()
        try:
            ok, _ = await AndroidPlatform.ensure_device()
            if ok:
                demo = await AndroidPlatform.full_demo(ws_path)
                dur = time.time() - t0
                result.stages.append(StageResult(
                    "Android demo", demo.success, dur,
                    "" if demo.success else demo.message[:200],
                ))
            else:
                result.stages.append(StageResult(
                    "Android demo", False, 0, "No device available",
                ))
        except Exception as exc:
            result.stages.append(StageResult(
                "Android demo", False, time.time() - t0, str(exc)[:200],
            ))

        # iOS demo
        from platforms import iOSPlatform
        t0 = time.time()
        try:
            ok, _ = await iOSPlatform.ensure_simulator()
            if ok:
                ios_loop = await run_agent_loop(
                    initial_prompt=(
                        "The Android target compiles. Now ensure the iOS target "
                        "also compiles. Fix any iOS-specific issues. "
                        "Only modify what's necessary for iOS compatibility. "
                        f"IMPORTANT: When running xcodebuild, always use: "
                        f"-destination 'name={config.IOS_SIMULATOR_NAME}'"
                    ),
                    workspace_key=slug,
                    workspace_path=ws_path,
                    claude=claude,
                    platform="ios",
                    on_status=loop_status,
                    budget=budget,
                )
                dur = time.time() - t0
                result.fix_loop_count += max(ios_loop.total_attempts - 1, 0)
                result.total_cost_usd = budget.total_cost_usd
                result.stages.append(StageResult(
                    "iOS build", ios_loop.success, dur,
                    "" if ios_loop.success else ios_loop.final_message[:200],
                ))
            else:
                result.stages.append(StageResult(
                    "iOS build", False, 0, "No simulator available",
                ))
        except Exception as exc:
            result.stages.append(StageResult(
                "iOS build", False, time.time() - t0, str(exc)[:200],
            ))

    # ── Finalize ─────────────────────────────────────────────────────────
    result.total_cost_usd = budget.total_cost_usd
    result.total_duration_secs = time.time() - overall_start
    result.success = all(s.passed for s in result.stages)

    _schedule_cleanup(result, registry, slug, ws_path, passed=result.success)
    return result


def _schedule_cleanup(
    result: SmokeTestResult,
    registry: WorkspaceRegistry,
    slug: str,
    ws_path: str,
    passed: bool,
) -> None:
    """Clean up workspace on pass; leave it for inspection on failure."""
    if passed:
        try:
            shutil.rmtree(ws_path)
        except Exception:
            pass
        registry.remove(slug)
        result.workspace_path = "(cleaned up)"
