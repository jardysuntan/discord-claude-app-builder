"""
commands/dashboard.py — iPhone-style app launcher page.

/dashboard          → build missing web apps + serve launcher
/dashboard rebuild  → force rebuild all web apps + serve

Architecture:
  /tmp/app-dashboard/
    index.html              (generated iPhone-style grid)
    app/<app-key>/          → symlink to each app's web dist dir
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Callable, Awaitable, Optional

import config
from platforms import WebPlatform, build_platform
from workspaces import WorkspaceRegistry

DASHBOARD_DIR = Path("/tmp/app-dashboard")
_dashboard_server_proc: Optional[asyncio.subprocess.Process] = None


async def handle_dashboard(
    registry: WorkspaceRegistry,
    on_status: Callable[[str, Optional[str]], Awaitable[None]],
    rebuild: bool = False,
) -> None:
    """Main handler for /dashboard command."""
    keys = registry.list_keys()
    if not keys:
        await on_status("No apps yet. Use `/buildapp <description>` to create one.", None)
        return

    await on_status(f"Building dashboard for {len(keys)} app(s)...", None)

    built_apps = []  # (key, display_name, has_web)
    for key in keys:
        ws_path = registry.get_path(key)
        if not ws_path:
            built_apps.append((key, key, False))
            continue

        dist_dir = WebPlatform._find_dist_dir(ws_path)

        if rebuild or dist_dir is None:
            await on_status(f"  Building web for **{key}**...", None)
            result = await build_platform("web", ws_path)
            if result.success:
                dist_dir = WebPlatform._find_dist_dir(ws_path)
            else:
                await on_status(f"  Web build failed for **{key}** — skipping.", None)

        built_apps.append((key, key, dist_dir is not None))

    # Set up dashboard directory with symlinks
    _setup_dashboard_dir(built_apps, registry)

    # Generate the HTML
    html = _generate_dashboard_html(built_apps)
    (DASHBOARD_DIR / "index.html").write_text(html)

    # Serve it
    url = await _serve_dashboard()
    built_count = sum(1 for _, _, has_web in built_apps if has_web)
    await on_status(
        f"Dashboard ready → {url}\n"
        f"  {built_count}/{len(built_apps)} apps available",
        None,
    )


def _setup_dashboard_dir(
    built_apps: list[tuple[str, str, bool]],
    registry: WorkspaceRegistry,
):
    """Create symlink structure in /tmp/app-dashboard/app/<key>/."""
    app_dir = DASHBOARD_DIR / "app"

    # Clean and recreate
    if DASHBOARD_DIR.exists():
        import shutil
        shutil.rmtree(DASHBOARD_DIR)
    DASHBOARD_DIR.mkdir(parents=True)
    app_dir.mkdir()

    for key, _, has_web in built_apps:
        if not has_web:
            continue
        ws_path = registry.get_path(key)
        if not ws_path:
            continue
        dist_dir = WebPlatform._find_dist_dir(ws_path)
        if dist_dir:
            symlink_path = app_dir / key
            symlink_path.symlink_to(dist_dir)


def _color_from_name(name: str) -> str:
    """Generate a consistent HSL color from app name."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16) % 360
    return f"hsl({h}, 60%, 50%)"


def _generate_dashboard_html(apps: list[tuple[str, str, bool]]) -> str:
    """Generate iPhone-style launcher HTML."""
    app_items = []
    for key, display_name, has_web in apps:
        color = _color_from_name(key)
        initials = display_name[:2].upper()

        if has_web:
            app_items.append(
                f'<a class="app" href="/app/{key}/index.html">'
                f'<div class="icon" style="background:{color}">{initials}</div>'
                f'<div class="label">{display_name}</div>'
                f'</a>'
            )
        else:
            app_items.append(
                f'<div class="app dimmed">'
                f'<div class="icon" style="background:{color};opacity:0.4">{initials}</div>'
                f'<div class="label">{display_name}</div>'
                f'<div class="badge">not built</div>'
                f'</div>'
            )

    grid = "\n        ".join(app_items)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>App Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #1c1c1e;
    color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    min-height: 100vh;
    padding: 40px 20px;
  }}
  h1 {{
    text-align: center;
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 32px;
    color: #f5f5f7;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 24px 16px;
    max-width: 400px;
    margin: 0 auto;
  }}
  .app {{
    display: flex;
    flex-direction: column;
    align-items: center;
    text-decoration: none;
    color: #fff;
    position: relative;
  }}
  .app.dimmed {{ opacity: 0.5; }}
  .icon {{
    width: 60px;
    height: 60px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }}
  a.app:active .icon {{
    transform: scale(0.9);
    transition: transform 0.1s;
  }}
  .label {{
    font-size: 11px;
    text-align: center;
    max-width: 70px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .badge {{
    font-size: 9px;
    color: #999;
    margin-top: 2px;
  }}
  @media (max-width: 360px) {{
    .grid {{ grid-template-columns: repeat(3, 1fr); }}
  }}
</style>
</head>
<body>
  <h1>Apps</h1>
  <div class="grid">
    {grid}
  </div>
</body>
</html>
"""


async def _serve_dashboard() -> str:
    """Start HTTP server for dashboard on DASHBOARD_PORT. Returns URL."""
    global _dashboard_server_proc

    # Stop existing
    if _dashboard_server_proc and _dashboard_server_proc.returncode is None:
        _dashboard_server_proc.terminate()
        try:
            await asyncio.wait_for(_dashboard_server_proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            _dashboard_server_proc.kill()

    _dashboard_server_proc = await asyncio.create_subprocess_exec(
        "python3", "-m", "http.server", str(config.DASHBOARD_PORT),
        "--directory", str(DASHBOARD_DIR),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(1)

    host = config.TAILSCALE_HOSTNAME or "localhost"
    return f"http://{host}:{config.DASHBOARD_PORT}"
