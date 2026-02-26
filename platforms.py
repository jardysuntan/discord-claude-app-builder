"""
platforms.py — Unified build, install, and demo for all KMP targets.

Each platform has:
  - build()       → compile the target
  - install()     → install/deploy to device/simulator/server
  - launch()      → start the app
  - screenshot()  → capture current screen
  - record()      → capture video (Android only for now)
  - get_demo_url() → URL for browser-based demo (Web, or mirror for Android)
"""

import asyncio
import os
import re
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config


@dataclass
class BuildResult:
    success: bool
    output: str
    error: str = ""


@dataclass
class DemoResult:
    success: bool
    message: str
    screenshot_path: Optional[str] = None
    video_path: Optional[str] = None
    demo_url: Optional[str] = None


# ── Shared helpers ───────────────────────────────────────────────────────────

async def _run(cmd: list[str], cwd: str = None, timeout: int = 60) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "Timed out"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def extract_build_error(raw_output: str, max_lines: int = 60) -> str:
    lines = raw_output.splitlines()
    # Gradle errors
    for i, line in enumerate(lines):
        if "FAILURE:" in line or "BUILD FAILED" in line:
            return "\n".join(lines[i:i + max_lines])
    # Xcode errors
    for i, line in enumerate(lines):
        if "error:" in line.lower() or "** BUILD FAILED **" in line:
            return "\n".join(lines[max(0, i - 5):i + max_lines])
    # Compiler errors
    error_lines = [l for l in lines if l.strip().startswith("e:") or "error:" in l.lower()]
    if error_lines:
        return "\n".join(error_lines[:max_lines])
    return "\n".join(lines[-max_lines:])


# ═══════════════════════════════════════════════════════════════════════════════
# ANDROID
# ═══════════════════════════════════════════════════════════════════════════════

class AndroidPlatform:

    @staticmethod
    async def ensure_device() -> tuple[bool, str]:
        rc, out, _ = await _run([config.ADB_BIN, "devices"])
        lines = [l for l in out.strip().splitlines()[1:] if "device" in l]
        if lines:
            return True, "Device connected."
        if not config.ANDROID_AVD:
            return False, "No device connected and ANDROID_AVD not set."
        await asyncio.create_subprocess_exec(
            config.EMULATOR_BIN, "-avd", config.ANDROID_AVD, "-no-snapshot-load",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        rc, _, _ = await _run([config.ADB_BIN, "wait-for-device"], timeout=90)
        if rc != 0:
            return False, "Emulator started but device didn't come online."
        for _ in range(30):
            rc, out, _ = await _run([config.ADB_BIN, "shell", "getprop", "sys.boot_completed"])
            if out.strip() == "1":
                return True, "Emulator booted."
            await asyncio.sleep(2)
        return False, "Emulator boot timed out."

    @staticmethod
    async def build(workspace_path: str) -> BuildResult:
        """Compile only — no device needed."""
        rc, out, err = await _run(
            ["./gradlew", "composeApp:assembleDebug"],
            cwd=workspace_path, timeout=300,
        )
        raw = out + err
        if rc == 0:
            return BuildResult(success=True, output=raw)
        return BuildResult(success=False, output=raw, error=extract_build_error(raw))

    @staticmethod
    async def install(workspace_path: str) -> BuildResult:
        """Install to connected device/emulator."""
        rc, out, err = await _run(
            ["./gradlew", "composeApp:installDebug"],
            cwd=workspace_path, timeout=120,
        )
        raw = out + err
        if rc == 0:
            return BuildResult(success=True, output=raw)
        return BuildResult(success=False, output=raw, error=extract_build_error(raw))

    @staticmethod
    def parse_app_id(workspace_path: str) -> Optional[str]:
        for name in ["composeApp/build.gradle.kts", "androidApp/build.gradle.kts",
                      "composeApp/build.gradle", "app/build.gradle.kts"]:
            gradle = Path(workspace_path) / name
            if gradle.exists():
                text = gradle.read_text()
                m = re.search(r'applicationId\s*=?\s*"([^"]+)"', text)
                if m:
                    return m.group(1)
        return None

    @staticmethod
    async def launch(workspace_path: str) -> str:
        app_id = AndroidPlatform.parse_app_id(workspace_path)
        if not app_id:
            return "Could not determine applicationId."
        await _run([
            config.ADB_BIN, "shell", "monkey",
            "-p", app_id, "-c", "android.intent.category.LAUNCHER", "1",
        ])
        await asyncio.sleep(2)
        return app_id

    @staticmethod
    async def screenshot() -> Optional[str]:
        rc, out, _ = await _run([config.ADB_BIN, "exec-out", "screencap", "-p"])
        if rc == 0 and out:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(out.encode("latin-1") if isinstance(out, str) else out)
            tmp.close()
            return tmp.name
        return None

    @staticmethod
    async def record(seconds: int = None) -> Optional[str]:
        seconds = seconds or config.SCREEN_RECORD_SECONDS
        device_path = "/sdcard/demo.mp4"
        await asyncio.create_subprocess_exec(
            config.ADB_BIN, "shell", "screenrecord",
            "--time-limit", str(seconds), device_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(seconds + 2)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        rc, _, _ = await _run([config.ADB_BIN, "pull", device_path, tmp.name])
        await _run([config.ADB_BIN, "shell", "rm", device_path])
        return tmp.name if rc == 0 else None

    @staticmethod
    async def full_demo(workspace_path: str) -> DemoResult:
        ok, msg = await AndroidPlatform.ensure_device()
        if not ok:
            return DemoResult(success=False, message=f"❌ {msg}")
        # install() compiles + pushes to device in one step
        result = await AndroidPlatform.install(workspace_path)
        if not result.success:
            return DemoResult(success=False, message=f"❌ Android build failed:\n```\n{result.error[:800]}\n```")
        app_id = await AndroidPlatform.launch(workspace_path)
        screenshot = await AndroidPlatform.screenshot()
        return DemoResult(
            success=True,
            message=f"✅ **{app_id}** running on Android emulator.",
            screenshot_path=screenshot,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# iOS
# ═══════════════════════════════════════════════════════════════════════════════

class iOSPlatform:

    @staticmethod
    async def ensure_simulator() -> tuple[bool, str]:
        """Boot the iOS simulator if not already running."""
        rc, out, _ = await _run([config.XCRUN, "simctl", "list", "devices", "booted"])
        if "Booted" in out:
            return True, "Simulator running."
        # Find the device UDID
        rc, out, _ = await _run([config.XCRUN, "simctl", "list", "devices", "-j"])
        # Try to boot by name
        await _run([config.XCRUN, "simctl", "boot", config.IOS_SIMULATOR_NAME])
        # Open Simulator.app to see it
        await _run(["open", "-a", "Simulator"])
        await asyncio.sleep(5)
        return True, f"Booted {config.IOS_SIMULATOR_NAME}."

    @staticmethod
    async def build(workspace_path: str) -> BuildResult:
        """Build the iOS target via Gradle's KMP iOS tasks."""
        # KMP projects use gradle to build the shared framework,
        # then xcodebuild for the final iOS app
        rc, out, err = await _run(
            ["./gradlew", "composeApp:linkDebugFrameworkIosSimulatorArm64"],
            cwd=workspace_path, timeout=300,
        )
        if rc != 0:
            raw = out + err
            return BuildResult(success=False, output=raw, error=extract_build_error(raw))

        # Now build the Xcode project
        ios_dir = Path(workspace_path) / "iosApp"
        if not ios_dir.exists():
            return BuildResult(success=False, output="", error="No iosApp/ directory found.")

        derived_data = Path(workspace_path) / "build" / "ios-simulator"
        rc, out, err = await _run([
            config.XCODEBUILD,
            "-project", str(ios_dir / "iosApp.xcodeproj"),
            "-scheme", "iosApp",
            "-sdk", "iphonesimulator",
            "-destination", f"name={config.IOS_SIMULATOR_NAME}",
            "-configuration", "Debug",
            "-derivedDataPath", str(derived_data),
            "build",
        ], cwd=workspace_path, timeout=300)
        raw = out + err
        if rc == 0:
            return BuildResult(success=True, output=raw)
        return BuildResult(success=False, output=raw, error=extract_build_error(raw))

    @staticmethod
    async def install_and_launch(workspace_path: str) -> str:
        """Find the built .app and install it on the simulator."""
        app_path = None

        # Check known derivedData path first (set by build())
        derived_data = Path(workspace_path) / "build" / "ios-simulator"
        products = derived_data / "Build" / "Products"
        if products.exists():
            for p in products.rglob("*.app"):
                if "Debug-iphonesimulator" in str(p):
                    app_path = p
                    break
            if not app_path:
                for p in products.rglob("*.app"):
                    app_path = p
                    break

        # Fallback: check default DerivedData
        if not app_path:
            derived = Path.home() / "Library" / "Developer" / "Xcode" / "DerivedData"
            for p in derived.rglob("iosApp.app"):
                if "Debug-iphonesimulator" in str(p):
                    app_path = p
                    break

        if not app_path:
            return "Could not find built .app bundle."

        # Install
        rc, out, err = await _run([config.XCRUN, "simctl", "install", "booted", str(app_path)])
        if rc != 0:
            return f"Install failed: {(out + err)[:200]}"

        # Launch — need bundle ID
        bundle_id = await iOSPlatform._get_bundle_id(app_path)
        if bundle_id:
            await _run([config.XCRUN, "simctl", "launch", "booted", bundle_id])
            await asyncio.sleep(2)
            return bundle_id
        return "Installed but could not determine bundle ID."

    @staticmethod
    async def _get_bundle_id(app_path: Path) -> Optional[str]:
        """Extract bundle ID from Info.plist."""
        plist = app_path / "Info.plist"
        if plist.exists():
            rc, out, _ = await _run([
                "/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier", str(plist)
            ])
            if rc == 0:
                return out.strip()
        return None

    @staticmethod
    async def check_crash(bundle_id: str) -> Optional[str]:
        """Check if the app crashed after launch. Returns crash log snippet or None if running."""
        # Check if the app process is still running on the simulator
        rc, out, _ = await _run([config.XCRUN, "simctl", "spawn", "booted", "launchctl", "list"])
        if rc == 0 and bundle_id in out:
            return None  # still running

        # App not running — grab most recent crash log
        import glob as glob_mod
        crash_dir = Path.home() / "Library" / "Logs" / "DiagnosticReports"
        app_name = bundle_id.split(".")[-1]
        candidates = []
        for pattern in [f"*{app_name}*", "*.ips"]:
            candidates.extend(glob_mod.glob(str(crash_dir / pattern)))
        if not candidates:
            return "App crashed on launch (no crash log found). Check startup code for runtime errors."

        # Most recent crash file
        candidates.sort(key=lambda f: Path(f).stat().st_mtime, reverse=True)
        try:
            content = Path(candidates[0]).read_text(errors="replace")
            return content[:1500]
        except Exception:
            return "App crashed on launch (could not read crash log)."

    @staticmethod
    async def screenshot() -> Optional[str]:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        rc, _, _ = await _run([
            config.XCRUN, "simctl", "io", "booted", "screenshot", tmp.name
        ])
        return tmp.name if rc == 0 else None

    @staticmethod
    async def record(seconds: int = None) -> Optional[str]:
        seconds = seconds or config.SCREEN_RECORD_SECONDS
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        proc = await asyncio.create_subprocess_exec(
            config.XCRUN, "simctl", "io", "booted", "recordVideo", tmp.name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(seconds)
        proc.send_signal(signal.SIGINT)  # graceful stop
        await proc.wait()
        return tmp.name

    @staticmethod
    async def full_demo(workspace_path: str) -> DemoResult:
        ok, msg = await iOSPlatform.ensure_simulator()
        if not ok:
            return DemoResult(success=False, message=f"❌ {msg}")
        result = await iOSPlatform.build(workspace_path)
        if not result.success:
            return DemoResult(success=False, message=f"❌ iOS build failed:\n```\n{result.error[:800]}\n```")
        bundle_id = await iOSPlatform.install_and_launch(workspace_path)
        screenshot = await iOSPlatform.screenshot()
        return DemoResult(
            success=True,
            message=f"✅ **{bundle_id}** running on iOS Simulator.",
            screenshot_path=screenshot,
        )

    # ── TestFlight helpers ────────────────────────────────────────────────

    @staticmethod
    def parse_bundle_id(workspace_path: str) -> Optional[str]:
        """Extract PRODUCT_BUNDLE_IDENTIFIER from Config.xcconfig."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        if not xcconfig.exists():
            return None
        text = xcconfig.read_text()
        m = re.search(r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*(.+)', text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def set_team_id(workspace_path: str, team_id: str) -> bool:
        """Set TEAM_ID in Config.xcconfig."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        if not xcconfig.exists():
            return False
        text = xcconfig.read_text()
        text = re.sub(r'TEAM_ID\s*=\s*.*', f'TEAM_ID={team_id}', text)
        xcconfig.write_text(text)
        return True

    @staticmethod
    def set_build_number(workspace_path: str, build_number: int) -> bool:
        """Update CURRENT_PROJECT_VERSION in Config.xcconfig."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        if not xcconfig.exists():
            return False
        text = xcconfig.read_text()
        text = re.sub(r'CURRENT_PROJECT_VERSION\s*=\s*\d+',
                      f'CURRENT_PROJECT_VERSION={build_number}', text)
        xcconfig.write_text(text)
        return True

    @staticmethod
    async def archive(workspace_path: str, team_id: str) -> BuildResult:
        """Build KMP release framework + create Xcode archive for distribution."""
        # Stage 1: Gradle release framework for arm64
        rc, out, err = await _run(
            ["./gradlew", "composeApp:linkReleaseFrameworkIosArm64"],
            cwd=workspace_path, timeout=600,
        )
        if rc != 0:
            raw = out + err
            return BuildResult(success=False, output=raw, error=extract_build_error(raw))

        # Stage 2: xcodebuild archive
        ios_dir = Path(workspace_path) / "iosApp"
        if not ios_dir.exists():
            return BuildResult(success=False, output="", error="No iosApp/ directory found.")

        archive_path = Path(workspace_path) / "build" / "iosApp.xcarchive"
        # Clean previous archive
        if archive_path.exists():
            import shutil
            shutil.rmtree(archive_path)

        rc, out, err = await _run([
            config.XCODEBUILD,
            "-project", str(ios_dir / "iosApp.xcodeproj"),
            "-scheme", "iosApp",
            "-sdk", "iphoneos",
            "-configuration", "Release",
            "-archivePath", str(archive_path),
            f"CODE_SIGN_STYLE=Automatic",
            f"DEVELOPMENT_TEAM={team_id}",
            "-allowProvisioningUpdates",
            "archive",
        ], cwd=workspace_path, timeout=600)

        raw = out + err
        if rc == 0 and archive_path.exists():
            return BuildResult(success=True, output=str(archive_path))
        return BuildResult(success=False, output=raw, error=extract_build_error(raw))


# ═══════════════════════════════════════════════════════════════════════════════
# WEB (Compose for Web / WASM)
# ═══════════════════════════════════════════════════════════════════════════════

_web_server_proc: Optional[asyncio.subprocess.Process] = None


class WebPlatform:

    @staticmethod
    async def build(workspace_path: str) -> BuildResult:
        """Build the WASM/JS web target."""
        # Try wasmJsBrowserDistribution first (Compose Multiplatform WASM)
        rc, out, err = await _run(
            ["./gradlew", "composeApp:wasmJsBrowserDistribution"],
            cwd=workspace_path, timeout=300,
        )
        raw = out + err
        if rc == 0:
            return BuildResult(success=True, output=raw)

        # Fallback to jsBrowserDistribution
        rc, out, err = await _run(
            ["./gradlew", "composeApp:jsBrowserDistribution"],
            cwd=workspace_path, timeout=300,
        )
        raw = out + err
        if rc == 0:
            return BuildResult(success=True, output=raw)

        return BuildResult(success=False, output=raw, error=extract_build_error(raw))

    @staticmethod
    def _find_dist_dir(workspace_path: str) -> Optional[Path]:
        """Find the built web distribution directory, generating index.html if needed."""
        candidates = [
            Path(workspace_path) / "composeApp" / "build" / "dist" / "wasmJs" / "productionExecutable",
            Path(workspace_path) / "composeApp" / "build" / "dist" / "js" / "productionExecutable",
            Path(workspace_path) / "composeApp" / "build" / "distributions",
        ]
        for d in candidates:
            if not d.exists():
                continue
            if any(d.glob("*.html")):
                return d
            # WASM builds may not include index.html — generate one
            js_files = list(d.glob("*.js"))
            wasm_files = list(d.glob("*.wasm"))
            if js_files and wasm_files:
                # Find the main entry JS (usually composeApp.js or similar)
                entry_js = None
                for js in js_files:
                    if "LICENSE" not in js.name and ".map" not in js.name:
                        entry_js = js.name
                        break
                if entry_js:
                    (d / "index.html").write_text(
                        '<!DOCTYPE html>\n<html><head>\n'
                        '<meta charset="UTF-8">\n'
                        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                        '<title>App</title>\n'
                        '<style>html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;}'
                        '#ComposeTarget{width:100%;height:100%}</style>\n'
                        '</head><body>\n'
                        '<canvas id="ComposeTarget"></canvas>\n'
                        f'<script src="{entry_js}"></script>\n'
                        '</body></html>\n'
                    )
                    return d
        return None

    @staticmethod
    async def serve(workspace_path: str) -> Optional[str]:
        """Start a simple HTTP server for the built web app."""
        global _web_server_proc

        # Stop existing server
        await WebPlatform.stop_server()

        dist_dir = WebPlatform._find_dist_dir(workspace_path)
        if not dist_dir:
            return None

        _web_server_proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "http.server", str(config.WEB_SERVE_PORT),
            "--directory", str(dist_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(1)

        host = config.TAILSCALE_HOSTNAME or "localhost"
        return f"http://{host}:{config.WEB_SERVE_PORT}"

    @staticmethod
    async def stop_server():
        global _web_server_proc
        if _web_server_proc and _web_server_proc.returncode is None:
            _web_server_proc.terminate()
            try:
                await asyncio.wait_for(_web_server_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _web_server_proc.kill()
            _web_server_proc = None

    @staticmethod
    async def full_demo(workspace_path: str) -> DemoResult:
        result = await WebPlatform.build(workspace_path)
        if not result.success:
            return DemoResult(success=False, message=f"❌ Web build failed:\n```\n{result.error[:800]}\n```")
        url = await WebPlatform.serve(workspace_path)
        if not url:
            return DemoResult(success=False, message="❌ Built but could not find distribution directory.")
        return DemoResult(
            success=True,
            message=f"✅ Web app live!",
            demo_url=url,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DEPLOY — install to physical devices
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DeployResult:
    success: bool
    message: str


async def deploy_ios(workspace_path: str) -> DeployResult:
    """Build for physical iPhone and install over WiFi/USB."""
    # 1. Find connected physical device
    rc, out, _ = await _run(
        [config.XCRUN, "devicectl", "list", "devices", "-j"],
        timeout=15,
    )
    if rc != 0:
        return DeployResult(False, "❌ `xcrun devicectl` failed. Is Xcode installed?")

    try:
        import json as _json
        data = _json.loads(out)
        devices = data.get("result", {}).get("devices", [])
        # Filter to connected, non-simulator devices
        physical = [
            d for d in devices
            if d.get("connectionProperties", {}).get("transportType") in ("wired", "localNetwork")
        ]
        if not physical:
            return DeployResult(False,
                "❌ No physical iPhone connected.\n"
                "Pair your iPhone via USB first, then enable WiFi deployment in Xcode."
            )
        device = physical[0]
        udid = device.get("hardwareProperties", {}).get("udid") or device.get("identifier", "")
        name = device.get("deviceProperties", {}).get("name", "iPhone")
    except Exception as e:
        return DeployResult(False, f"❌ Failed to parse device list: {e}")

    # 2. Build KMP framework for physical device (arm64, not simulator)
    rc, out, err = await _run(
        ["./gradlew", "composeApp:linkDebugFrameworkIosArm64"],
        cwd=workspace_path, timeout=300,
    )
    if rc != 0:
        raw = out + err
        return DeployResult(False, f"❌ KMP framework build failed:\n```\n{extract_build_error(raw)[:800]}\n```")

    # 3. Build Xcode project for physical device
    ios_dir = Path(workspace_path) / "iosApp"
    if not ios_dir.exists():
        return DeployResult(False, "❌ No `iosApp/` directory found.")

    derived_data = Path(workspace_path) / "build" / "ios-device"
    rc, out, err = await _run([
        config.XCODEBUILD,
        "-project", str(ios_dir / "iosApp.xcodeproj"),
        "-scheme", "iosApp",
        "-sdk", "iphoneos",
        "-configuration", "Debug",
        "-derivedDataPath", str(derived_data),
        "-allowProvisioningUpdates",
        "build",
    ], cwd=workspace_path, timeout=300)
    raw = out + err
    if rc != 0:
        return DeployResult(False, f"❌ Xcode build failed:\n```\n{extract_build_error(raw)[:800]}\n```")

    # 4. Find the .app bundle
    app_path = None
    products = derived_data / "Build" / "Products"
    for p in products.rglob("*.app"):
        if "iphoneos" in str(p).lower() or "Debug-iphoneos" in str(p.parent.name):
            app_path = p
            break
    if not app_path:
        for p in products.rglob("*.app"):
            app_path = p
            break
    if not app_path:
        return DeployResult(False, "❌ Build succeeded but couldn't find .app bundle.")

    # 5. Install to device
    rc, out, err = await _run([
        config.XCRUN, "devicectl", "device", "install", "app",
        "--device", udid, str(app_path),
    ], timeout=60)
    if rc != 0:
        return DeployResult(False, f"❌ Install failed:\n```\n{(out + err)[:800]}\n```")

    return DeployResult(True, f"✅ Installed on **{name}** (`{udid[:12]}…`)\nOpen the app on your iPhone!")


async def deploy_android(workspace_path: str) -> DeployResult:
    """Build and install to a physical Android device over USB/WiFi."""
    # Check for physical device (not emulator)
    rc, out, _ = await _run([config.ADB_BIN, "devices"])
    lines = [l for l in out.strip().splitlines()[1:] if l.strip() and "device" in l]
    if not lines:
        return DeployResult(False, "❌ No Android device connected. Plug in via USB or use `adb connect`.")

    # installDebug will build + install
    rc, out, err = await _run(
        ["./gradlew", "composeApp:installDebug"],
        cwd=workspace_path, timeout=300,
    )
    raw = out + err
    if rc != 0:
        return DeployResult(False, f"❌ Build/install failed:\n```\n{extract_build_error(raw)[:800]}\n```")

    app_id = AndroidPlatform.parse_app_id(workspace_path) or "the app"
    return DeployResult(True, f"✅ **{app_id}** installed on your Android device.\nOpen it on your phone!")


# ═══════════════════════════════════════════════════════════════════════════════
# TESTFLIGHT — archive, export IPA, upload
# ═══════════════════════════════════════════════════════════════════════════════

async def export_ipa(archive_path: str, workspace_path: str, team_id: str) -> tuple[bool, str, str]:
    """Export IPA from xcarchive. Returns (success, ipa_path_or_error, raw_output)."""
    export_dir = Path(workspace_path) / "build" / "ipa"
    if export_dir.exists():
        import shutil
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    # Generate ExportOptions.plist
    plist_path = Path(workspace_path) / "build" / "ExportOptions.plist"
    plist_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        '    <key>method</key>\n    <string>app-store</string>\n'
        f'    <key>teamID</key>\n    <string>{team_id}</string>\n'
        '    <key>signingStyle</key>\n    <string>automatic</string>\n'
        '    <key>uploadSymbols</key>\n    <true/>\n'
        '    <key>destination</key>\n    <string>export</string>\n'
        '</dict>\n</plist>\n'
    )

    rc, out, err = await _run([
        config.XCODEBUILD,
        "-exportArchive",
        "-archivePath", archive_path,
        "-exportOptionsPlist", str(plist_path),
        "-exportPath", str(export_dir),
        "-allowProvisioningUpdates",
    ], timeout=300)

    raw = out + err
    if rc != 0:
        return False, extract_build_error(raw), raw

    # Find the .ipa file
    ipas = list(export_dir.glob("*.ipa"))
    if ipas:
        return True, str(ipas[0]), raw
    return False, "Archive exported but no .ipa found", raw


async def testflight_upload(ipa_path: str, key_id: str, issuer_id: str) -> DeployResult:
    """Upload IPA to App Store Connect / TestFlight."""
    rc, out, err = await _run([
        config.XCRUN, "altool",
        "--upload-app",
        "-f", ipa_path,
        "-t", "ios",
        "--apiKey", key_id,
        "--apiIssuer", issuer_id,
    ], timeout=600)

    raw = out + err
    if rc == 0:
        return DeployResult(True, "Upload to TestFlight succeeded.")
    if "No suitable application records" in raw:
        return DeployResult(False,
            "❌ No app record found in App Store Connect.\n"
            "Create one at https://appstoreconnect.apple.com with the matching bundle ID.")
    if "Unable to authenticate" in raw or "auth" in raw.lower():
        return DeployResult(False,
            "❌ Authentication failed. Check ASC_KEY_ID, ASC_ISSUER_ID, "
            "and that your .p8 file is at ~/.private_keys/AuthKey_<KEY_ID>.p8")
    return DeployResult(False, f"❌ Upload failed:\n```\n{raw[:800]}\n```")


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


async def demo_platform(platform: str, workspace_path: str) -> DemoResult:
    cls = PLATFORMS.get(platform)
    if not cls:
        return DemoResult(success=False, message=f"Unknown platform: {platform}")
    return await cls.full_demo(workspace_path)
