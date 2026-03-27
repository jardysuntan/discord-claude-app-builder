"""
helpers/smoketest_runner.py — Core smoke-test logic.

Runs a full buildapp → demo cycle with deterministic prompts,
validates every stage, records results, and cleans up on pass.

Supports multiple scenarios (counter, map, video) that each run
independently through the full pipeline.

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

# ── Scenarios ─────────────────────────────────────────────────────────────────

SMOKE_SCENARIOS: list[dict[str, str]] = [
    {
        "name": "counter",
        "prompt": "a counter app with increment and decrement buttons and a reset button",
    },
    {
        "name": "map",
        "prompt": (
            "a location finder app that shows a map with 5 coffee shop markers "
            "in San Francisco using Leaflet.js, with a list of venues below the map"
        ),
    },
    {
        "name": "video",
        "prompt": (
            "a short video feed app (TikTok-style) with vertical swipe between "
            "3 sample videos from public URLs, with play/pause on tap and a progress bar"
        ),
    },
]

SCENARIO_NAMES = [s["name"] for s in SMOKE_SCENARIOS]


def _get_scenarios(filter_names: list[str] | None = None) -> list[dict[str, str]]:
    """Return scenarios matching filter, or all if filter is None."""
    if not filter_names:
        return SMOKE_SCENARIOS
    return [s for s in SMOKE_SCENARIOS if s["name"] in filter_names]


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
            lines.append("\u2705 **Smoke Test: PASS**")
        else:
            lines.append("\U0001f6a8 **Smoke Test: FAIL** \U0001f6a8")
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


async def _run_scenario(
    scenario_name: str,
    prompt: str,
    registry: WorkspaceRegistry,
    claude: ClaudeRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    is_admin: bool,
    owner_id: Optional[int],
) -> SmokeTestResult:
    """Run a single scenario through the full pipeline. Returns its own SmokeTestResult."""

    app_name = f"SmokeTest{scenario_name.capitalize()}"
    prefix = f"{scenario_name.capitalize()}"

    result = SmokeTestResult()
    overall_start = time.time()
    budget = BudgetTracker(
        max_cost_usd=config.MAX_FIX_BUDGET_USD,
        max_invocations=config.MAX_TOTAL_INVOCATIONS,
    )

    async def status(msg, file_path=None):
        await on_status(msg, file_path)

    # ── Stage 1: Create workspace ────────────────────────────────────────
    await status(f"\U0001f9ea **{prefix}** smoke test starting...", None)

    t0 = time.time()
    scaffold = await create_kmp_project(app_name, registry, owner_id=owner_id)
    dur = time.time() - t0

    if not scaffold.success or not scaffold.slug:
        result.stages.append(StageResult(
            f"{prefix}: Create workspace", False, dur, scaffold.message,
        ))
        result.total_duration_secs = time.time() - overall_start
        return result

    result.stages.append(StageResult(f"{prefix}: Create workspace", True, dur))
    slug = scaffold.slug
    result.workspace_slug = slug
    ws_path = registry.get_path(slug)
    result.workspace_path = ws_path or ""

    if not ws_path:
        result.stages.append(StageResult(
            f"{prefix}: Resolve workspace", False, 0, f"Path not found for {slug}",
        ))
        result.total_duration_secs = time.time() - overall_start
        return result

    await status(f"Created workspace **{slug}**", None)

    # ── Stage 2: Claude generates code (Android) ─────────────────────────
    feature_prompt = build_feature_prompt(app_name, prompt)

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
        f"{prefix}: Claude response", claude_ok, 0,
        "" if claude_ok else loop_result.final_message[:200],
    ))
    if not claude_ok:
        result.total_duration_secs = time.time() - overall_start
        _schedule_cleanup(result, registry, slug, ws_path, passed=False)
        return result

    # Code generated stage
    result.stages.append(StageResult(f"{prefix}: Code generated", True, 0))

    # Build passes stage
    result.stages.append(StageResult(
        f"{prefix}: Android build", loop_result.success, dur,
        "" if loop_result.success else loop_result.final_message[:200],
    ))
    if not loop_result.success:
        result.total_duration_secs = time.time() - overall_start
        _schedule_cleanup(result, registry, slug, ws_path, passed=False)
        return result

    await status(f"{prefix}: Android build passed", None)

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
        f"{prefix}: Web build", web_loop.success, dur,
        "" if web_loop.success else web_loop.final_message[:200],
    ))
    if not web_loop.success:
        result.total_duration_secs = time.time() - overall_start
        _schedule_cleanup(result, registry, slug, ws_path, passed=False)
        return result

    await status(f"{prefix}: Web build passed", None)

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
        f"{prefix}: Screenshot captured", screenshot_ok, dur,
        "" if screenshot_ok else "Web server or screenshot failed",
    ))

    if screenshot_ok:
        await status(f"{prefix}: Screenshot captured", screenshot_path)

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
                    f"{prefix}: Android demo", demo.success, dur,
                    "" if demo.success else demo.message[:200],
                ))
            else:
                result.stages.append(StageResult(
                    f"{prefix}: Android demo", False, 0, "No device available",
                ))
        except Exception as exc:
            result.stages.append(StageResult(
                f"{prefix}: Android demo", False, time.time() - t0, str(exc)[:200],
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
                    f"{prefix}: iOS build", ios_loop.success, dur,
                    "" if ios_loop.success else ios_loop.final_message[:200],
                ))
            else:
                result.stages.append(StageResult(
                    f"{prefix}: iOS build", False, 0, "No simulator available",
                ))
        except Exception as exc:
            result.stages.append(StageResult(
                f"{prefix}: iOS build", False, time.time() - t0, str(exc)[:200],
            ))

    # ── Finalize ─────────────────────────────────────────────────────────
    result.total_cost_usd = budget.total_cost_usd
    result.total_duration_secs = time.time() - overall_start
    result.success = all(s.passed for s in result.stages)

    _schedule_cleanup(result, registry, slug, ws_path, passed=result.success)
    return result


async def run_smoketest(
    registry: WorkspaceRegistry,
    claude: ClaudeRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    is_admin: bool = False,
    owner_id: Optional[int] = None,
    scenarios: list[str] | None = None,
) -> SmokeTestResult:
    """Run smoke test scenarios. Returns a combined SmokeTestResult.

    *scenarios*: list of scenario names to run (e.g. ["counter", "map"]).
                 None or empty means run all scenarios.
    """
    selected = _get_scenarios(scenarios)

    if len(selected) == 1:
        # Single scenario — run directly and return its result
        s = selected[0]
        return await _run_scenario(
            s["name"], s["prompt"], registry, claude,
            on_status, is_admin, owner_id,
        )

    # Multiple scenarios — run sequentially and merge results
    combined = SmokeTestResult()
    overall_start = time.time()

    for s in selected:
        scenario_result = await _run_scenario(
            s["name"], s["prompt"], registry, claude,
            on_status, is_admin, owner_id,
        )
        combined.stages.extend(scenario_result.stages)
        combined.fix_loop_count += scenario_result.fix_loop_count
        combined.total_cost_usd += scenario_result.total_cost_usd
        # Keep last workspace info
        if scenario_result.workspace_slug:
            combined.workspace_slug = scenario_result.workspace_slug
            combined.workspace_path = scenario_result.workspace_path

    combined.total_duration_secs = time.time() - overall_start
    combined.success = all(s.passed for s in combined.stages)
    return combined


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
