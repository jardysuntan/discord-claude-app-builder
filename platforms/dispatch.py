"""
platforms.dispatch — route a platform string to the right platform class.
"""

from platforms.common import BuildResult, DemoResult
from platforms.android import AndroidPlatform
from platforms.ios import iOSPlatform
from platforms.web import WebPlatform


# ═══════════════════════════════════════════════════════════════════════════════
# DISPATCHER — route by platform string
# ═══════════════════════════════════════════════════════════════════════════════

PLATFORMS = {
    "android": AndroidPlatform,
    "ios": iOSPlatform,
    "web": WebPlatform,
}


async def build_platform(platform: str, workspace_path: str) -> BuildResult:
    cls = PLATFORMS.get(platform)
    if not cls:
        return BuildResult(success=False, output="", error=f"Unknown platform: {platform}")
    return await cls.build(workspace_path)


async def demo_platform(platform: str, workspace_path: str, workspace_key: str = "") -> DemoResult:
    cls = PLATFORMS.get(platform)
    if not cls:
        return DemoResult(success=False, message=f"Unknown platform: {platform}")
    if platform == "web":
        return await cls.full_demo(workspace_path, workspace_key=workspace_key)
    return await cls.full_demo(workspace_path)
