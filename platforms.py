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
import hashlib
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
    async def clear_logcat():
        """Clear logcat buffer for clean crash detection."""
        await _run([config.ADB_BIN, "logcat", "-c"])

    @staticmethod
    async def check_crash(app_id: str) -> Optional[str]:
        """Check logcat for FATAL EXCEPTION near app_id. Returns crash snippet or None."""
        rc, out, _ = await _run([
            config.ADB_BIN, "logcat", "-d",
            "-s", "AndroidRuntime:E", "ActivityManager:E",
        ])
        if rc != 0 or not out.strip():
            return None
        # Look for FATAL EXCEPTION lines mentioning our app
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if "FATAL EXCEPTION" in line and app_id in out[out.index(line):]:
                snippet = "\n".join(lines[i:i + 30])
                return snippet[:1500]
        return None

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

    # ── Play Store helpers ────────────────────────────────────────────────

    @staticmethod
    def _find_gradle_file(workspace_path: str) -> Optional[Path]:
        """Find the composeApp build.gradle.kts (or fallback variants)."""
        for name in ["composeApp/build.gradle.kts", "androidApp/build.gradle.kts",
                      "composeApp/build.gradle", "app/build.gradle.kts"]:
            p = Path(workspace_path) / name
            if p.exists():
                return p
        return None

    @staticmethod
    def set_version_code(workspace_path: str, code: int) -> bool:
        """Set versionCode in build.gradle.kts."""
        gradle = AndroidPlatform._find_gradle_file(workspace_path)
        if not gradle:
            return False
        text = gradle.read_text()
        new_text = re.sub(r'versionCode\s*=?\s*\d+', f'versionCode = {code}', text)
        if new_text == text:
            # No existing versionCode — inject after applicationId
            new_text = re.sub(
                r'(applicationId\s*=?\s*"[^"]+")',
                rf'\1\n            versionCode = {code}',
                text,
            )
        gradle.write_text(new_text)
        return True

    @staticmethod
    def inject_signing_config(workspace_path: str, ks_path: str, alias: str,
                              store_pw: str, key_pw: str) -> bool:
        """Inject signingConfigs block and wire it to release buildType. Skip if already present."""
        gradle = AndroidPlatform._find_gradle_file(workspace_path)
        if not gradle:
            return False
        text = gradle.read_text()
        if "signingConfigs" in text:
            return True  # already configured

        signing_block = (
            '    signingConfigs {\n'
            '        create("release") {\n'
            f'            storeFile = file("{ks_path}")\n'
            f'            storePassword = "{store_pw}"\n'
            f'            keyAlias = "{alias}"\n'
            f'            keyPassword = "{key_pw}"\n'
            '        }\n'
            '    }\n'
        )

        # Insert signingConfigs before buildTypes (or at end of android block)
        if "buildTypes" in text:
            text = text.replace("buildTypes", signing_block + "    buildTypes", 1)
        else:
            # Insert before the closing brace of android { }
            text = re.sub(r'(android\s*\{.*?)(^\})', rf'\1{signing_block}\2',
                          text, count=1, flags=re.DOTALL | re.MULTILINE)

        # Wire release buildType to use the signing config
        if 'release {' in text or 'release{' in text:
            # Add signingConfig inside existing release block
            text = re.sub(
                r'(release\s*\{)',
                r'\1\n                signingConfig = signingConfigs.getByName("release")',
                text, count=1,
            )
        else:
            # No release buildType — add one after signingConfigs
            release_block = (
                '    buildTypes {\n'
                '        release {\n'
                '            signingConfig = signingConfigs.getByName("release")\n'
                '        }\n'
                '    }\n'
            )
            if "buildTypes" not in text:
                text = text.replace(signing_block, signing_block + release_block)

        gradle.write_text(text)
        return True

    @staticmethod
    async def bundle_release(workspace_path: str) -> BuildResult:
        """Run bundleRelease and return the AAB path."""
        rc, out, err = await _run(
            ["./gradlew", "composeApp:bundleRelease"],
            cwd=workspace_path, timeout=600,
        )
        raw = out + err
        if rc != 0:
            return BuildResult(success=False, output=raw, error=extract_build_error(raw))

        # Find the .aab file
        aab_dir = Path(workspace_path) / "composeApp" / "build" / "outputs" / "bundle" / "release"
        aabs = list(aab_dir.glob("*.aab")) if aab_dir.exists() else []
        if not aabs:
            # Fallback search
            for aab in Path(workspace_path).rglob("*.aab"):
                if "release" in str(aab):
                    aabs = [aab]
                    break
        if aabs:
            return BuildResult(success=True, output=str(aabs[0]))
        return BuildResult(success=False, output=raw, error="Build succeeded but no .aab found")

    @staticmethod
    async def generate_keystore(workspace_path: str, alias: str, password: str) -> tuple[bool, str]:
        """Generate a release keystore in the workspace. Returns (success, keystore_path)."""
        ks_path = str(Path(workspace_path) / "release.keystore")
        if Path(ks_path).exists():
            return True, ks_path

        rc, out, err = await _run([
            "keytool", "-genkeypair",
            "-v",
            "-keystore", ks_path,
            "-alias", alias,
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass", password,
            "-keypass", password,
            "-dname", "CN=App,O=App,L=Unknown,ST=Unknown,C=US",
        ], timeout=30)
        if rc == 0:
            return True, ks_path
        return False, (out + err)[:500]


def preflight_fix_android(workspace_path: str) -> list[str]:
    """Auto-fix common Play Store issues. Returns list of fixes applied."""
    fixes = []
    gradle = AndroidPlatform._find_gradle_file(workspace_path)
    if not gradle:
        return fixes

    text = gradle.read_text()

    # Ensure minSdk >= 21
    m = re.search(r'minSdk\s*=?\s*(\d+)', text)
    if m and int(m.group(1)) < 21:
        text = re.sub(r'minSdk\s*=?\s*\d+', 'minSdk = 21', text)
        fixes.append(f"Bumped minSdk from {m.group(1)} to 21")
        gradle.write_text(text)

    # Ensure app icon exists (res/mipmap-hdpi)
    res_dir = Path(workspace_path) / "composeApp" / "src" / "androidMain" / "res"
    mipmap = res_dir / "mipmap-hdpi"
    icon = mipmap / "ic_launcher.png"
    if not icon.exists():
        mipmap.mkdir(parents=True, exist_ok=True)
        # Create a minimal 72x72 green PNG (valid 1-pixel scaled)
        template_icon = Path(config.TEMPLATES_DIR).expanduser() / "kmp" / "KMPTemplate" / "composeApp" / "src" / "androidMain" / "res" / "mipmap-hdpi" / "ic_launcher.png"
        if template_icon.exists():
            import shutil as _shutil
            _shutil.copy2(template_icon, icon)
            fixes.append("Added default Android app icon")

    return fixes


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
        """Extract PRODUCT_BUNDLE_IDENTIFIER from Config.xcconfig or project.pbxproj."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        xcconfig_vars: dict[str, str] = {}

        # Parse all xcconfig variables for resolving references
        if xcconfig.exists():
            text = xcconfig.read_text()
            for km in re.finditer(r'^(\w+)\s*=\s*(.+)$', text, re.MULTILINE):
                xcconfig_vars[km.group(1)] = km.group(2).strip()

        def _resolve(val: str) -> Optional[str]:
            """Resolve $(VAR) references using xcconfig_vars. Return None if unresolvable."""
            def replacer(m):
                return xcconfig_vars.get(m.group(1), m.group(0))
            resolved = re.sub(r'\$\((\w+)\)', replacer, val)
            if "$(" in resolved:
                return None  # still has unresolved refs
            return resolved

        # Try PRODUCT_BUNDLE_IDENTIFIER from xcconfig
        pbi = xcconfig_vars.get("PRODUCT_BUNDLE_IDENTIFIER")
        if pbi:
            resolved = _resolve(pbi)
            if resolved:
                return resolved

        # Fall back to pbxproj
        pbxproj = Path(workspace_path) / "iosApp" / "iosApp.xcodeproj" / "project.pbxproj"
        if pbxproj.exists():
            text = pbxproj.read_text()
            m = re.search(r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*"?([^";]+)"?\s*;', text)
            if m:
                resolved = _resolve(m.group(1).strip())
                if resolved:
                    return resolved

        # Last resort: derive from package prefix + workspace name
        ws_name = Path(workspace_path).name.lower()
        ws_name = re.sub(r'[^a-z0-9]', '', ws_name)
        if ws_name:
            return f"{config.KMP_PACKAGE_PREFIX}.{ws_name}"
        return None

    @staticmethod
    def set_bundle_id(workspace_path: str, bundle_id: str) -> bool:
        """Ensure PRODUCT_BUNDLE_IDENTIFIER is set in xcconfig (and any referenced vars)."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        if not xcconfig.exists():
            return False
        text = xcconfig.read_text()
        # If pbxproj uses $(BUNDLE_ID), define it in xcconfig
        if "BUNDLE_ID" not in text:
            text += f"\nBUNDLE_ID={bundle_id}\n"
            xcconfig.write_text(text)
            return True
        # If BUNDLE_ID exists but is empty, set it
        if re.search(r'^BUNDLE_ID\s*=\s*$', text, re.MULTILINE):
            text = re.sub(r'BUNDLE_ID\s*=\s*$', f'BUNDLE_ID={bundle_id}', text, flags=re.MULTILINE)
            xcconfig.write_text(text)
            return True
        return False

    @staticmethod
    def set_team_id(workspace_path: str, team_id: str) -> bool:
        """Set TEAM_ID in Config.xcconfig or DEVELOPMENT_TEAM in pbxproj."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        if xcconfig.exists():
            text = xcconfig.read_text()
            text = re.sub(r'TEAM_ID\s*=\s*.*', f'TEAM_ID={team_id}', text)
            xcconfig.write_text(text)
            return True
        pbxproj = Path(workspace_path) / "iosApp" / "iosApp.xcodeproj" / "project.pbxproj"
        if pbxproj.exists():
            text = pbxproj.read_text()
            text = re.sub(r'DEVELOPMENT_TEAM\s*=\s*"?[^"]*"?\s*;',
                          f'DEVELOPMENT_TEAM = {team_id};', text)
            pbxproj.write_text(text)
            return True
        return False

    @staticmethod
    def set_build_number(workspace_path: str, build_number: int) -> bool:
        """Update CURRENT_PROJECT_VERSION in Config.xcconfig or pbxproj."""
        xcconfig = Path(workspace_path) / "iosApp" / "Configuration" / "Config.xcconfig"
        if xcconfig.exists():
            text = xcconfig.read_text()
            text = re.sub(r'CURRENT_PROJECT_VERSION\s*=\s*\d+',
                          f'CURRENT_PROJECT_VERSION={build_number}', text)
            xcconfig.write_text(text)
            return True
        pbxproj = Path(workspace_path) / "iosApp" / "iosApp.xcodeproj" / "project.pbxproj"
        if pbxproj.exists():
            text = pbxproj.read_text()
            text = re.sub(r'CURRENT_PROJECT_VERSION\s*=\s*\d+\s*;',
                          f'CURRENT_PROJECT_VERSION = {build_number};', text)
            pbxproj.write_text(text)
            return True
        return False

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
_tunnel_proc: Optional[asyncio.subprocess.Process] = None


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

        # Only try JS fallback if the WASM task itself doesn't exist
        if "not found in project" in raw and "wasmJsBrowserDistribution" in raw:
            rc2, out2, err2 = await _run(
                ["./gradlew", "composeApp:jsBrowserDistribution"],
                cwd=workspace_path, timeout=300,
            )
            raw2 = out2 + err2
            if rc2 == 0:
                return BuildResult(success=True, output=raw2)
            # JS fallback also missing — return original WASM error
            if "not found in project" in raw2:
                return BuildResult(success=False, output=raw, error=extract_build_error(raw))
            return BuildResult(success=False, output=raw2, error=extract_build_error(raw2))

        # WASM task exists but build failed — return the real error
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
    async def _kill_port(port: int):
        """Kill any process listening on the given port."""
        rc, out, _ = await _run(["lsof", "-ti", f":{port}"])
        if rc == 0 and out.strip():
            for pid in out.strip().splitlines():
                pid = pid.strip()
                if pid.isdigit():
                    await _run(["kill", "-9", pid])
            await asyncio.sleep(0.5)

    @staticmethod
    async def serve(workspace_path: str) -> Optional[str]:
        """Start a simple HTTP server for the built web app."""
        global _web_server_proc, _tunnel_proc

        # Stop existing server (ours + any orphan on the port)
        await WebPlatform.stop_server()
        await WebPlatform._kill_port(config.WEB_SERVE_PORT)

        dist_dir = WebPlatform._find_dist_dir(workspace_path)
        if not dist_dir:
            return None

        # Custom server with COOP/COEP headers required for Kotlin/WASM SharedArrayBuffer
        server_script = (
            "import http.server,sys,os\n"
            "os.chdir(sys.argv[2])\n"
            "class H(http.server.SimpleHTTPRequestHandler):\n"
            "  def end_headers(self):\n"
            "    self.send_header('Cross-Origin-Opener-Policy','same-origin')\n"
            "    self.send_header('Cross-Origin-Embedder-Policy','require-corp')\n"
            "    super().end_headers()\n"
            "  def log_message(self,*a):pass\n"
            "http.server.HTTPServer(('',int(sys.argv[1])),H).serve_forever()\n"
        )
        _web_server_proc = await asyncio.create_subprocess_exec(
            "python3", "-c", server_script,
            str(config.WEB_SERVE_PORT), str(dist_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(1)

        # Try to start a Cloudflare Tunnel for a public URL
        public_url = await WebPlatform._start_tunnel()
        if public_url:
            return public_url

        # Fallback to Tailscale / localhost
        host = config.TAILSCALE_HOSTNAME or "localhost"
        return f"http://{host}:{config.WEB_SERVE_PORT}"

    @staticmethod
    async def _start_tunnel() -> Optional[str]:
        """Start a cloudflared quick tunnel. Returns public URL or None."""
        global _tunnel_proc
        await WebPlatform._stop_tunnel()

        import shutil
        if not shutil.which("cloudflared"):
            return None

        _tunnel_proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://localhost:{config.WEB_SERVE_PORT}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # cloudflared prints the public URL to stderr
        # Wait up to 15 seconds for the URL to appear
        url = None
        import re as _re
        try:
            deadline = asyncio.get_event_loop().time() + 15
            buffer = b""
            while asyncio.get_event_loop().time() < deadline:
                try:
                    chunk = await asyncio.wait_for(_tunnel_proc.stderr.read(4096), timeout=2)
                    if not chunk:
                        break
                    buffer += chunk
                    m = _re.search(rb"https://[a-zA-Z0-9-]+\.trycloudflare\.com", buffer)
                    if m:
                        url = m.group(0).decode()
                        break
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

        if not url:
            await WebPlatform._stop_tunnel()
        return url

    @staticmethod
    async def _stop_tunnel():
        global _tunnel_proc
        if _tunnel_proc and _tunnel_proc.returncode is None:
            _tunnel_proc.terminate()
            try:
                await asyncio.wait_for(_tunnel_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _tunnel_proc.kill()
            _tunnel_proc = None

    @staticmethod
    async def stop_server():
        global _web_server_proc
        await WebPlatform._stop_tunnel()
        if _web_server_proc and _web_server_proc.returncode is None:
            _web_server_proc.terminate()
            try:
                await asyncio.wait_for(_web_server_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _web_server_proc.kill()
            _web_server_proc = None

    @staticmethod
    async def check_health(url: str) -> Optional[str]:
        """HTTP GET the url; returns error string or None if healthy."""
        import urllib.request
        loop = asyncio.get_event_loop()
        try:
            def _fetch():
                resp = urllib.request.urlopen(url, timeout=10)
                body = resp.read()
                if resp.status != 200:
                    return f"HTTP {resp.status}"
                if len(body) < 50:
                    return "Response body too small (likely empty page)"
                return None
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            return str(e)

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
# TESTFLIGHT — pre-flight fixes, archive, export IPA, validate, upload
# ═══════════════════════════════════════════════════════════════════════════════


import json as _json
import plistlib
import shutil


def _ensure_asset_catalog_in_pbxproj(pbxproj_path: Path) -> bool:
    """Add Assets.xcassets to an Xcode project if not already referenced.

    Edits the pbxproj text to insert:
      - A PBXFileReference for Assets.xcassets
      - A PBXBuildFile linking it to Resources
      - An entry in the iosApp PBXGroup children
      - An entry in PBXResourcesBuildPhase files

    Returns True if the file was modified.
    """
    text = pbxproj_path.read_text()
    if "Assets.xcassets" in text:
        return False

    # Generate deterministic 24-char hex IDs
    seed = str(pbxproj_path)
    file_ref_id = hashlib.md5((seed + "asset_fileref").encode()).hexdigest()[:24].upper()
    build_file_id = hashlib.md5((seed + "asset_buildfile").encode()).hexdigest()[:24].upper()

    # 1. Add PBXBuildFile entry
    build_file_line = (
        f"\t\t{build_file_id} /* Assets.xcassets in Resources */ = "
        f"{{isa = PBXBuildFile; fileRef = {file_ref_id} /* Assets.xcassets */; }};\n"
    )
    text = text.replace(
        "/* End PBXBuildFile section */",
        build_file_line + "/* End PBXBuildFile section */",
    )

    # 2. Add PBXFileReference entry
    file_ref_line = (
        f"\t\t{file_ref_id} /* Assets.xcassets */ = "
        f"{{isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; "
        f"path = Assets.xcassets; sourceTree = \"<group>\"; }};\n"
    )
    text = text.replace(
        "/* End PBXFileReference section */",
        file_ref_line + "/* End PBXFileReference section */",
    )

    # 3. Add to the iosApp PBXGroup children (the group with path = iosApp)
    # Find: children = (\n ... ); \n path = iosApp;
    group_pattern = re.compile(
        r"(isa = PBXGroup;\s*children = \(\s*\n)(.*?)(^\s*\);\s*\n\s*path = iosApp;)",
        re.MULTILINE | re.DOTALL,
    )
    match = group_pattern.search(text)
    if match:
        indent = "\t\t\t\t"
        new_child = f"{indent}{file_ref_id} /* Assets.xcassets */,\n"
        text = text[:match.end(2)] + new_child + text[match.end(2):]

    # 4. Add to PBXResourcesBuildPhase files
    resources_pattern = re.compile(
        r"(isa = PBXResourcesBuildPhase;.*?files = \(\s*\n?)(.*?)(\s*\);)",
        re.DOTALL,
    )
    match = resources_pattern.search(text)
    if match:
        indent = "\t\t\t\t"
        new_file = f"{indent}{build_file_id} /* Assets.xcassets in Resources */,\n"
        # Insert into files list
        existing = match.group(2)
        text = (
            text[:match.start(2)]
            + existing + new_file
            + text[match.end(2):]
        )

    pbxproj_path.write_text(text)
    return True


def preflight_fix_ios(workspace_path: str) -> list[str]:
    """Auto-fix common TestFlight validation issues. Returns list of fixes applied."""
    fixes = []
    ios_dir = Path(workspace_path) / "iosApp"
    if not ios_dir.exists():
        return fixes

    # ── 1. Ensure app icon exists ─────────────────────────────────────────
    icon_dir = ios_dir / "iosApp" / "Assets.xcassets" / "AppIcon.appiconset"
    icon_dir.mkdir(parents=True, exist_ok=True)
    icon_png = icon_dir / "app-icon-1024.png"

    if not icon_png.exists():
        # Copy default icon from template
        template_icon = Path(config.TEMPLATES_DIR).expanduser() / "kmp" / "KMPTemplate" / "iosApp" / "iosApp" / "Assets.xcassets" / "AppIcon.appiconset" / "app-icon-1024.png"
        if template_icon.exists():
            shutil.copy2(template_icon, icon_png)
            fixes.append("Added default app icon")

    # Ensure Contents.json references the icon file
    contents_json = icon_dir / "Contents.json"
    contents_json.write_text(_json.dumps({
        "images": [
            {
                "filename": "app-icon-1024.png",
                "idiom": "universal",
                "platform": "ios",
                "size": "1024x1024",
            }
        ],
        "info": {"author": "xcode", "version": 1},
    }, indent=2))

    # ── 1b. Ensure Assets.xcassets is in the Xcode project ───────────────
    pbxproj = ios_dir / "iosApp.xcodeproj" / "project.pbxproj"
    if pbxproj.exists():
        if _ensure_asset_catalog_in_pbxproj(pbxproj):
            fixes.append("Added Assets.xcassets to Xcode project")

    # ── 2. Fix Info.plist ─────────────────────────────────────────────────
    plist_path = ios_dir / "iosApp" / "Info.plist"
    if plist_path.exists():
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
    else:
        plist = {}

    changed = False

    # Add CFBundleIconName if missing
    if "CFBundleIconName" not in plist:
        plist["CFBundleIconName"] = "AppIcon"
        fixes.append("Added CFBundleIconName to Info.plist")
        changed = True

    # Skip export compliance question on TestFlight (no custom encryption)
    if not plist.get("ITSAppUsesNonExemptEncryption", True):
        pass  # already set to False
    else:
        plist["ITSAppUsesNonExemptEncryption"] = False
        changed = True

    # Ensure iPad orientations include UpsideDown
    ipad_key = "UISupportedInterfaceOrientations~ipad"
    required_ipad = [
        "UIInterfaceOrientationPortrait",
        "UIInterfaceOrientationPortraitUpsideDown",
        "UIInterfaceOrientationLandscapeLeft",
        "UIInterfaceOrientationLandscapeRight",
    ]
    current_ipad = plist.get(ipad_key, [])
    if "UIInterfaceOrientationPortraitUpsideDown" not in current_ipad:
        plist[ipad_key] = required_ipad
        fixes.append("Added iPad orientation support")
        changed = True

    # Ensure iPhone orientations exist
    iphone_key = "UISupportedInterfaceOrientations"
    if iphone_key not in plist:
        plist[iphone_key] = [
            "UIInterfaceOrientationPortrait",
            "UIInterfaceOrientationLandscapeLeft",
            "UIInterfaceOrientationLandscapeRight",
        ]
        changed = True

    if changed:
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)

    return fixes


async def validate_ipa(ipa_path: str, key_id: str, issuer_id: str) -> tuple[bool, str]:
    """Validate IPA before uploading. Returns (success, error_message)."""
    rc, out, err = await _run([
        config.XCRUN, "altool",
        "--validate-app",
        "-f", ipa_path,
        "-t", "ios",
        "--apiKey", key_id,
        "--apiIssuer", issuer_id,
    ], timeout=300)
    raw = out + err
    if rc == 0:
        return True, ""
    return False, raw

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
