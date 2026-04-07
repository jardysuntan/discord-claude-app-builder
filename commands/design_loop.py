"""
commands/design_loop.py — Visual diff loop: auto-iterate UI until it matches a reference.

/build-from-design  (with an image attachment)
  -> Claude builds UI from reference -> screenshot -> compare -> fix -> repeat
"""

import time
from typing import Callable, Awaitable, Optional

import config
from agent_protocol import AgentRunner
from workspaces import WorkspaceRegistry
from agent_loop import run_agent_loop, format_loop_summary
from platforms import build_platform, WebPlatform
from helpers.screenshot_compare import (
    take_app_screenshot,
    build_design_comparison_prompt,
    parse_similarity_score,
)
from helpers.web_screenshot import take_web_screenshot
from workspace_spec import format_spec_context, load_workspace_spec


SIMILARITY_THRESHOLD = 95


async def handle_build_from_design(
    reference_image_path: str,
    description: str,
    registry: WorkspaceRegistry,
    claude: AgentRunner,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    user_id: int,
    max_iterations: int | None = None,
) -> dict:
    """Run the visual diff loop: build UI from reference, then iterate.

    Returns a dict with keys: success, iterations, final_score,
    reference_path, final_screenshot_path.
    """
    max_iterations = max_iterations or config.MAX_DESIGN_ITERATIONS

    ws_key, ws_path = registry.resolve(None, user_id)
    if not ws_key or not ws_path:
        await on_status("No workspace set. Use `/use <ws>` first.", None)
        return {"success": False, "iterations": 0, "final_score": 0}

    spec = load_workspace_spec(ws_path)
    context_prefix = format_spec_context(spec) if spec else ""

    start_time = time.time()

    # Step 1: Initial build from the reference design
    await on_status(
        "🎨 Building UI from reference design...", None,
    )

    initial_prompt = (
        f"Build the UI to match this reference design image.\n"
        f"Read this image file: {reference_image_path}\n\n"
        f"Replicate the visual design as closely as possible — layout, "
        f"colors, typography, spacing, icons, and all visual elements.\n"
        f"Use Material 3 Compose Multiplatform components.\n"
    )
    if description:
        initial_prompt += f"\nAdditional context from the user: {description}\n"

    async def loop_status(msg):
        await on_status(msg, None)

    loop_result = await run_agent_loop(
        initial_prompt=initial_prompt,
        workspace_key=ws_key,
        workspace_path=ws_path,
        claude=claude,
        platform="web",
        on_status=loop_status,
        context_prefix=context_prefix,
    )

    summary = format_loop_summary(loop_result)
    await on_status(summary, None)

    if not loop_result.success:
        await on_status("Web build failed on initial design build.", None)
        return {
            "success": False,
            "iterations": 0,
            "final_score": 0,
            "reference_path": reference_image_path,
            "final_screenshot_path": None,
        }

    # Serve the web build so we can screenshot it
    url = await WebPlatform.serve(ws_path, workspace_key=ws_key)
    if not url:
        await on_status("Could not start web server for screenshots.", None)
        return {
            "success": False,
            "iterations": 0,
            "final_score": 0,
            "reference_path": reference_image_path,
            "final_screenshot_path": None,
        }

    # Step 2-5: Visual comparison loop
    final_score = 0
    final_screenshot = None
    iteration = 0

    for iteration in range(1, max_iterations + 1):
        await on_status(
            f"📸 Visual diff iteration {iteration}/{max_iterations} — "
            f"capturing screenshot...",
            None,
        )

        # Take screenshot of current app
        app_screenshot = await take_web_screenshot(
            f"http://localhost:{config.WEB_SERVE_PORT}",
        )
        if not app_screenshot:
            app_screenshot = await take_app_screenshot(path="/")
        if not app_screenshot:
            await on_status(
                f"Could not capture app screenshot on iteration {iteration}.", None,
            )
            break

        final_screenshot = app_screenshot

        # Build comparison prompt for Claude vision
        comparison_prompt = build_design_comparison_prompt(
            reference_image_path=reference_image_path,
            actual_screenshot_path=app_screenshot,
            iteration=iteration,
            max_iterations=max_iterations,
        )

        # Ask Claude to compare and fix
        await on_status(
            f"🔍 Comparing reference vs actual (iteration {iteration})...",
            None,
        )

        compare_result = await claude.run(
            comparison_prompt,
            ws_key,
            ws_path,
            context_prefix=context_prefix,
            on_progress=loop_status,
        )

        if compare_result.exit_code != 0:
            await on_status(
                f"Claude comparison failed on iteration {iteration}.", None,
            )
            break

        # Parse similarity score
        score = parse_similarity_score(compare_result.stdout)
        if score is not None:
            final_score = score
            await on_status(
                f"📊 Similarity: **{score}%** (threshold: {SIMILARITY_THRESHOLD}%)",
                None,
            )

            if score >= SIMILARITY_THRESHOLD:
                await on_status(
                    f"Design converged at {score}% similarity after "
                    f"{iteration} iteration(s).",
                    None,
                )
                break
        else:
            await on_status(
                "Could not parse similarity score — continuing with fixes.",
                None,
            )

        # Rebuild web after Claude's fixes
        await on_status(f"🔨 Rebuilding after iteration {iteration} fixes...", None)
        web_result = await build_platform("web", ws_path)
        if not web_result.success:
            # Auto-fix the web build
            fix_loop = await run_agent_loop(
                initial_prompt=(
                    "The wasmJs web build failed after visual fixes. "
                    "Fix the code so it compiles.\n\n"
                    f"```\n{web_result.error[:800]}\n```"
                ),
                workspace_key=ws_key,
                workspace_path=ws_path,
                claude=claude,
                platform="web",
                max_attempts=2,
                on_status=loop_status,
                context_prefix=context_prefix,
            )
            if not fix_loop.success:
                await on_status(
                    f"Web build failed after iteration {iteration} — stopping.",
                    None,
                )
                break

        # Re-serve after rebuild
        await WebPlatform.serve(ws_path, workspace_key=ws_key)

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    converged = final_score >= SIMILARITY_THRESHOLD

    return {
        "success": converged,
        "iterations": iteration,
        "final_score": final_score,
        "reference_path": reference_image_path,
        "final_screenshot_path": final_screenshot,
        "elapsed": f"{mins}m {secs}s",
    }
