"""
platforms.android — Android build / install / demo / Play Store + deploy.
"""

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Optional

import config

from platforms.common import (
    BuildResult,
    DemoResult,
    DeployResult,
    _run,
    extract_build_error,
)


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
    def bump_version_name(workspace_path: str) -> str | None:
        """Increment versionName (e.g. "1.0" → "1.1"). Returns new version or None."""
        gradle = AndroidPlatform._find_gradle_file(workspace_path)
        if not gradle:
            return None
        text = gradle.read_text()
        m = re.search(r'versionName\s*=?\s*"([^"]+)"', text)
        if m:
            old = m.group(1)
            # Parse "X.Y" and increment Y
            parts = old.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                new_name = f"{parts[0]}.{int(parts[1]) + 1}"
            else:
                new_name = f"{old}.1"
            new_text = text.replace(m.group(0), f'versionName = "{new_name}"')
            gradle.write_text(new_text)
            return new_name
        else:
            # No versionName — inject after versionCode
            new_text = re.sub(
                r'(versionCode\s*=?\s*\d+)',
                r'\1\n            versionName = "1.0"',
                text,
            )
            gradle.write_text(new_text)
            return "1.0"

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
