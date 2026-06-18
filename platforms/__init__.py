"""
platforms — Unified build, install, and demo for all KMP targets.

This package was split out of the former single-file `platforms.py` module.
It re-exports every public name so existing importers keep working unchanged:

    from platforms import build_platform, demo_platform, WebPlatform
    import platforms; platforms.AndroidPlatform

Each platform exposes:
  - build()        → compile the target
  - install()      → install/deploy to device/simulator/server
  - launch()       → start the app
  - screenshot()   → capture current screen
  - record()       → capture video (Android + iOS)
  - get_demo_url() → URL for browser-based demo (Web, or mirror for Android)
"""

from platforms.common import (
    BuildResult,
    DemoResult,
    DeployResult,
    _run,
    extract_build_error,
)
from platforms.android import (
    AndroidPlatform,
    preflight_fix_android,
    deploy_android,
)
from platforms.ios import (
    iOSPlatform,
    _ensure_asset_catalog_in_pbxproj,
    preflight_fix_ios,
    validate_ipa,
    export_ipa,
    deploy_ios,
    testflight_upload,
)
from platforms.web import WebPlatform
from platforms.dispatch import (
    PLATFORMS,
    build_platform,
    demo_platform,
)

__all__ = [
    # common
    "BuildResult",
    "DemoResult",
    "DeployResult",
    "_run",
    "extract_build_error",
    # android
    "AndroidPlatform",
    "preflight_fix_android",
    "deploy_android",
    # ios
    "iOSPlatform",
    "_ensure_asset_catalog_in_pbxproj",
    "preflight_fix_ios",
    "validate_ipa",
    "export_ipa",
    "deploy_ios",
    "testflight_upload",
    # web
    "WebPlatform",
    # dispatch
    "PLATFORMS",
    "build_platform",
    "demo_platform",
]
