"""
helpers/screenshot_compare.py — Screenshot comparison for visual bug fixing.

Takes screenshots of the running web app and builds prompts that help Claude
understand visual differences between the user's screenshot and the current app state.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

import config


async def take_app_screenshot(
    path: str = "/",
    wait_ms: int = 3000,
) -> str | None:
    """Take a screenshot of the running web app at the given route.

    *path* is appended to ``http://localhost:{WEB_SERVE_PORT}`` so callers can
    target specific screens (e.g. ``/settings``, ``/login``).

    Returns the file path to the PNG screenshot, or ``None`` on failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    # Normalise path so it always starts with /
    if not path.startswith("/"):
        path = f"/{path}"

    url = f"http://localhost:{config.WEB_SERVE_PORT}{path}"
    screenshot_path = Path(tempfile.gettempdir()) / "app_screenshot.png"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 390, "height": 844})
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            # Extra wait for WASM / Compose for Web to finish rendering
            await asyncio.sleep(wait_ms / 1000)
            await page.screenshot(path=str(screenshot_path), full_page=False)
            await browser.close()
        return str(screenshot_path) if screenshot_path.exists() else None
    except Exception:
        return None


def _guess_route_from_text(text: str) -> str:
    """Try to extract a route/screen name from the user's message.

    Looks for patterns like "on the /settings page", "the login screen",
    "/profile route", etc.  Returns ``"/"`` when nothing is detected.
    """
    # Explicit route mention  e.g. "/settings", "/about"
    m = re.search(r"(?:on|at|the|navigate to|go to)?\s*(/[a-zA-Z0-9_/-]+)", text)
    if m:
        return m.group(1)

    # Named screen reference  e.g. "settings screen", "login page"
    m = re.search(
        r"\b([\w-]+)\s+(?:screen|page|route|view|tab)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        name = m.group(1).lower()
        # Ignore filler words that aren't real screen names
        if name not in {"the", "this", "that", "a", "an", "main", "first", "same", "home"}:
            return f"/{name}"

    return "/"


def build_visual_diff_prompt(
    user_image_paths: list[str],
    bot_screenshot_path: str | None,
) -> str:
    """Return a prompt section that asks Claude to compare screenshots.

    *user_image_paths*  — images the user uploaded (bugs / desired state).
    *bot_screenshot_path* — screenshot of the app's current state (may be None).
    """
    lines: list[str] = []

    lines.append(
        "## Visual Bug Report — Screenshot Comparison\n"
    )

    # User images
    lines.append(
        f"The user attached {len(user_image_paths)} screenshot(s) showing "
        "a bug or the desired UI. Read these files:\n"
    )
    for p in user_image_paths:
        lines.append(f"  - {p}")

    # Bot screenshot
    if bot_screenshot_path:
        lines.append(
            f"\nThe app's CURRENT state has been captured automatically:\n"
            f"  - {bot_screenshot_path}\n"
        )
        lines.append(
            "Compare the user's screenshot(s) against the current app screenshot.\n"
            "Identify every visual difference — layout, colours, spacing, missing "
            "elements, broken styling, wrong text, etc.\n"
            "Then fix the code so the app matches what the user expects."
        )
    else:
        lines.append(
            "\n(Could not capture a live screenshot of the app — compare against "
            "your understanding of the codebase instead.)\n"
            "Identify the visual issues shown in the user's screenshot(s) and fix "
            "the code to resolve them."
        )

    return "\n".join(lines)


def build_design_comparison_prompt(
    reference_image_path: str,
    actual_screenshot_path: str,
    iteration: int,
    max_iterations: int,
) -> str:
    """Return a prompt that asks Claude to compare a reference design against
    the current app screenshot and generate targeted fixes.

    Claude's response should include a similarity score (0-100) on the first
    line in the format ``SIMILARITY: <score>`` followed by fix instructions.
    """
    return (
        "## Visual Design Comparison — Iteration "
        f"{iteration}/{max_iterations}\n\n"
        f"**Reference design** (what the app SHOULD look like):\n"
        f"  Read this image file: {reference_image_path}\n\n"
        f"**Current app screenshot** (what the app ACTUALLY looks like):\n"
        f"  Read this image file: {actual_screenshot_path}\n\n"
        "Compare these two images carefully. Evaluate:\n"
        "1. Layout and structure (component placement, spacing, alignment)\n"
        "2. Colors and theming (backgrounds, text colors, accent colors)\n"
        "3. Typography (font sizes, weights, text content)\n"
        "4. Missing or extra elements\n"
        "5. Icons, images, and decorative elements\n"
        "6. Overall visual fidelity\n\n"
        "**IMPORTANT:** Start your response with EXACTLY this line:\n"
        "SIMILARITY: <score>\n"
        "where <score> is a number from 0 to 100 representing how closely "
        "the current app matches the reference design.\n\n"
        "Then describe every visual difference you see and fix the code "
        "so the app matches the reference design as closely as possible. "
        "Focus on the most impactful differences first."
    )


def parse_similarity_score(claude_output: str) -> int | None:
    """Extract the SIMILARITY score from Claude's response.

    Returns an integer 0–100, or None if not found.
    """
    for line in claude_output.splitlines():
        line = line.strip()
        if line.upper().startswith("SIMILARITY:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                return None
    return None
