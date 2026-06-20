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


def build_annotation_prompt(
    annotated_image_paths: list[str],
    original_screenshot_paths: list[str],
    bot_screenshot_path: str | None,
) -> str:
    """Return a prompt for annotated-screenshot visual design feedback.

    *annotated_image_paths* — images the user drew on (circles, arrows, text).
    *original_screenshot_paths* — the original bot screenshot(s) the user replied to.
    *bot_screenshot_path* — fresh screenshot of the app's current state (may be None).
    """
    lines: list[str] = []

    lines.append("## Visual Design Mode — Annotated Screenshot Feedback\n")

    lines.append(
        "The user replied to a screenshot with annotated image(s). They drew on "
        "the screenshot using circles, arrows, highlights, or text to indicate "
        "exactly which parts of the UI they want changed.\n"
    )

    lines.append("**Annotated image(s) from the user** (read these files):\n")
    for p in annotated_image_paths:
        lines.append(f"  - {p}")

    if original_screenshot_paths:
        lines.append("\n**Original screenshot(s) the user annotated on** (read these files):\n")
        for p in original_screenshot_paths:
            lines.append(f"  - {p}")

    if bot_screenshot_path:
        lines.append(
            f"\n**Current live app state** (auto-captured):\n"
            f"  - {bot_screenshot_path}\n"
        )

    lines.append(
        "\n**Instructions:**\n"
        "1. Compare the annotated image against the original screenshot to identify "
        "every annotation the user added — circles, arrows, text labels, highlights, "
        "crossed-out areas, or drawn shapes.\n"
        "2. For each annotation, determine the spatial region it targets and what "
        "change the user is requesting (e.g. an arrow pointing to a button with "
        "text \"make bigger\", a circle around misaligned elements, etc.).\n"
        "3. Generate targeted, scoped code changes for each annotated region. "
        "Only modify the UI components that correspond to the annotated areas.\n"
        "4. If the user wrote text on the annotation, treat it as an explicit "
        "instruction for that area."
    )

    return "\n".join(lines)
