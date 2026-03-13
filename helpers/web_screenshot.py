"""
helpers/web_screenshot.py — Take a screenshot of a web URL using Playwright.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


async def take_web_screenshot(url: str, wait_ms: int = 3000) -> str | None:
    """Take a screenshot of *url* and return the file path, or None on failure.

    Uses Playwright with headless Chromium. Waits *wait_ms* for the page to
    render (WASM apps need time to load).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    screenshot_path = Path(tempfile.gettempdir()) / "web_preview.png"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 390, "height": 844})
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # Extra wait for WASM/Compose to render
            await asyncio.sleep(wait_ms / 1000)
            await page.screenshot(path=str(screenshot_path))
            await browser.close()
        return str(screenshot_path) if screenshot_path.exists() else None
    except Exception:
        return None
