"""
commands/create.py — Scaffold a new Kotlin Multiplatform (Compose) project.

Creates a KMP project with:
  - composeApp/     → shared UI (Android + iOS + Web)
  - iosApp/         → Xcode project wrapper
  - CLAUDE.md       → project memory for Claude Code

Two scaffolding strategies:
  1. Copy from local template (TEMPLATES_DIR/kmp/KmpTemplate)
  2. If no template: generate a minimal KMP project structure
"""

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import config
from workspaces import WorkspaceRegistry


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class CreateResult:
    message: str
    slug: str | None = None
    success: bool = False


def _unique_name(app_name: str, base_dir: Path) -> str:
    """If slug dir already exists, append 2, 3, … until unique."""
    if not (base_dir / slugify(app_name)).exists():
        return app_name
    for i in range(2, 100):
        candidate = f"{app_name}{i}"
        if not (base_dir / slugify(candidate)).exists():
            return candidate
    return f"{app_name}{int(time.time())}"


def _project_base_dir(owner_id: int | None) -> Path:
    """Return the base directory for a user's projects.
    Admin (or None) → BASE_PROJECTS_DIR, others → BASE_PROJECTS_DIR/users/<id>/"""
    base = Path(config.BASE_PROJECTS_DIR)
    if owner_id is None or owner_id == config.DISCORD_ALLOWED_USER_ID:
        return base
    user_dir = base / "users" / str(owner_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


async def create_kmp_project(app_name: str, registry: WorkspaceRegistry, owner_id: int | None = None) -> CreateResult:
    """Scaffold a new KMP Compose Multiplatform project."""
    base_dir = _project_base_dir(owner_id)
    app_name = _unique_name(app_name, base_dir)
    slug = slugify(app_name)
    new_pkg = f"{config.KMP_PACKAGE_PREFIX}.{slug}"
    # Use slug for directory name (no spaces) — spaces break Kotlin/JS webpack
    project_dir = base_dir / slug
    template_dir = Path(config.TEMPLATES_DIR) / "kmp" / "KmpTemplate"

    if template_dir.exists():
        # Strategy 1: Copy from template
        shutil.copytree(template_dir, project_dir)
        _rewrite_packages(project_dir, config.TEMPLATE_OLD_PKG, new_pkg, app_name)
    else:
        # Strategy 2: Generate minimal structure
        _generate_minimal_kmp(project_dir, new_pkg, app_name)

    # Create CLAUDE.md for project memory
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(
        f"# {app_name}\n\n"
        f"Kotlin Multiplatform (Compose Multiplatform) project.\n\n"
        f"## Package\n`{new_pkg}`\n\n"
        f"## Targets\n"
        f"- Android (composeApp)\n"
        f"- iOS (iosApp, via Xcode)\n"
        f"- Web/WASM (composeApp wasmJs)\n\n"
        f"## Build commands\n"
        f"- Android: `./gradlew composeApp:installDebug`\n"
        f"- iOS framework: `./gradlew composeApp:linkDebugFrameworkIosSimulatorArm64`\n"
        f"- Web: `./gradlew composeApp:wasmJsBrowserDistribution`\n\n"
        f"## Architecture\n"
        f"- Shared UI in `composeApp/src/commonMain/` using Compose Multiplatform\n"
        f"- Platform-specific code in `androidMain/`, `iosMain/`, `wasmJsMain/`\n"
        f"- Use `expect`/`actual` for platform APIs\n\n"
        f"## iOS Widget Extension\n"
        f"- Location: `iosApp/WidgetExtension/`\n"
        f"- Pure SwiftUI + WidgetKit (NOT Compose — widgets can't use Compose)\n"
        f"- Can `import ComposeApp` to access shared KMP framework for data/logic\n"
        f"- Entry point: `WidgetExtensionBundle.swift`, widget code: `AppWidget.swift`\n\n"
        f"## UI Rules\n"
        f"- Do NOT use emoji characters (Unicode emoji) in UI text — they render as broken boxes on the Web (WASM) target. Use Material Icons from `androidx.compose.material.icons.Icons` instead.\n\n"
        f"## Native Components (expect/actual)\n"
        f"Compose Multiplatform shares most UI, but these features MUST use\n"
        f"`expect`/`actual` with native platform implementations.\n"
        f"Use shared Compose for everything else.\n\n"
        f"| Component | Why | Android (actual) | iOS (actual) |\n"
        f"|-----------|-----|-------------------|-------------|\n"
        f"| Maps | No CMP maps component — use Leaflet.js in PlatformWebViewHtml (see Maps section below) | PlatformWebViewHtml | PlatformWebViewHtml |\n"
        f"| Camera | No CMP camera API | CameraX | AVCaptureSession via UIKitView |\n"
        f"| Text input (complex forms) | iOS keyboard bugs (scroll, recomposition) | Compose TextField (fine) | UITextField via UIKitView for chat/search |\n"
        f"| Push notifications | Entirely platform-native | Firebase Cloud Messaging | UNUserNotificationCenter + APNs |\n"
        f"| Biometrics | Hardware security APIs | BiometricPrompt | LocalAuthentication (Face ID/Touch ID) |\n"
        f"| Navigation (iOS feel) | iOS back-swipe gesture missing in CMP | Compose Navigation (fine) | UINavigationController for native transitions |\n"
        f"| WebView | Android-only in CMP | android.webkit.WebView | WKWebView via UIKitView |\n"
        f"| Video player | No CMP media player | Media3/ExoPlayer | AVPlayerViewController via UIKitView |\n"
        f"| File/document picker | System dialog, platform-native | ActivityResultContracts | UIDocumentPickerViewController |\n"
        f"| Share sheet | No CMP abstraction | Intent.ACTION_SEND | UIActivityViewController |\n"
        f"| Date/time pickers | iOS wheel style expected, Material3 DatePicker is Android-only | Material3 DatePicker | UIDatePicker via UIKitView |\n"
        f"| Location services | Platform-specific APIs | Fused Location Provider | CoreLocation (CLLocationManager) |\n"
        f"| Permissions | Different models per platform | ActivityResultContracts | Framework-specific (AVFoundation, CoreLocation, etc.) |\n"
        f"| In-app purchases | Platform billing APIs | Google Play Billing Library | StoreKit 2 |\n"
        f"| Widgets/extensions | CMP cannot target extension points | Glance Compose | WidgetKit (SwiftUI only) |\n\n"
        f"**Pattern:** declare `expect` in `commonMain`, implement `actual` in\n"
        f"`androidMain` and `iosMain`. For UI components, use `AndroidView`/`UIKitView`\n"
        f"interop to wrap native views inside Compose.\n\n"
        f"**Safe for shared Compose:** buttons, lists, forms, cards, text, images,\n"
        f"icons, dialogs, bottom sheets, tabs, top bars, snackbars, progress indicators,\n"
        f"lazy lists/grids, basic animations, theming, navigation (Android).\n\n"
        f"## Maps (Leaflet.js in WebView)\n"
        f"Maps MUST use Leaflet.js rendered inside PlatformWebViewHtml (the expect/actual WebView wrapper).\n"
        f"**CRITICAL: NEVER load Leaflet JS/CSS from a CDN** — WebViews on iOS and web block external resource loading.\n"
        f"Instead, inline the Leaflet CSS directly in a `<style>` tag and the Leaflet JS as a gzip+base64 blob decoded in-browser:\n"
        f"1. Store Leaflet CSS as a Kotlin string constant (it's ~8KB minified)\n"
        f"2. Store Leaflet JS as base64 in a Kotlin string constant (~197KB)\n"
        f"3. In the HTML, decode with `atob()` and inject via `script.textContent` in a separate `<script>` block\n"
        f"4. Put the map init code in a second `<script>` tag (runs after Leaflet is loaded)\n"
        f"5. Map tiles load from `https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png` (these work fine from JS at runtime)\n"
        f"See `platform/LeafletBundle.kt` for a working example if one exists in this project.\n\n"
        f"## Supabase\n"
        f"- Schema and seed data live in `supabase/schema.sql` and `supabase/seed.sql`\n"
        f"- **To run SQL against the live database**, use: `python /Users/jaredtanpersonal/bots/discord-claude-bridge/run_supabase_sql.py \"<SQL>\"`\n"
        f"- Always update BOTH the local SQL files AND run the SQL against the live database\n"
        f"- For schema changes: edit `schema.sql` AND run `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` via the script\n"
        f"- For data changes: edit `seed.sql` AND run the `UPDATE`/`INSERT` via the script\n"
        f"- **Never tell users to go to the Supabase dashboard or run SQL manually** — do it directly\n\n"
        f"## Decisions\n"
        f"(Claude will append notes here)\n"
    )

    # Register workspace
    registry.add(slug, str(project_dir), owner_id=owner_id)

    return CreateResult(
        message=(
            f"✅ Created **{app_name}** (Kotlin Multiplatform)\n"
            f"  Package: `{new_pkg}`\n"
            f"  Targets: Android · iOS · Web\n"
            f"  Path: `{project_dir}`\n"
            f"  Workspace: `{slug}`\n\n"
            f"Use `/use {slug}` then start prompting.\n"
            f"Build with `/build android`, `/build ios`, or `/build web`."
        ),
        slug=slug,
        success=True,
    )


def _rewrite_packages(project_dir: Path, old_pkg: str, new_pkg: str, app_name: str):
    """Rewrite package names and app labels in a copied template."""
    old_path = old_pkg.replace(".", os.sep)
    new_path = new_pkg.replace(".", os.sep)

    # Text replacement in source files
    for ext in ("*.kt", "*.kts", "*.xml", "*.plist", "*.swift", "*.pbxproj", "*.xcconfig"):
        for fpath in project_dir.rglob(ext):
            if fpath.is_file():
                try:
                    text = fpath.read_text()
                    if old_pkg in text:
                        fpath.write_text(text.replace(old_pkg, new_pkg))
                except (UnicodeDecodeError, PermissionError):
                    pass

    # Replace template app name with actual app name
    template_name = "KMPTemplate"
    if app_name != template_name:
        for ext in ("*.swift", "*.pbxproj", "*.xcconfig", "*.kts"):
            for fpath in project_dir.rglob(ext):
                if fpath.is_file():
                    try:
                        text = fpath.read_text()
                        if template_name in text:
                            fpath.write_text(text.replace(template_name, app_name))
                    except (UnicodeDecodeError, PermissionError):
                        pass

    # Move source directories
    for src_set in ["commonMain", "androidMain", "iosMain", "wasmJsMain", "desktopMain"]:
        old_src = project_dir / "composeApp" / "src" / src_set / "kotlin" / old_path
        new_src = project_dir / "composeApp" / "src" / src_set / "kotlin" / new_path
        if old_src.exists() and not new_src.exists():
            new_src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_src), str(new_src))

    # Update app name in strings
    strings_xml = project_dir / "composeApp" / "src" / "androidMain" / "res" / "values" / "strings.xml"
    if strings_xml.exists():
        text = strings_xml.read_text()
        text = re.sub(r'<string name="app_name">.*?</string>',
                       f'<string name="app_name">{app_name}</string>', text)
        strings_xml.write_text(text)


def _generate_minimal_kmp(project_dir: Path, pkg: str, app_name: str):
    """Generate a minimal KMP Compose project from scratch."""
    pkg_path = pkg.replace(".", "/")

    project_dir.mkdir(parents=True)

    # settings.gradle.kts
    (project_dir / "settings.gradle.kts").write_text(
        f'rootProject.name = "{app_name}"\n'
        f'include(":composeApp")\n'
    )

    # Root build.gradle.kts
    (project_dir / "build.gradle.kts").write_text(
        'plugins {\n'
        '    alias(libs.plugins.kotlinMultiplatform) apply false\n'
        '    alias(libs.plugins.composeMultiplatform) apply false\n'
        '    alias(libs.plugins.composeCompiler) apply false\n'
        '    alias(libs.plugins.androidApplication) apply false\n'
        '}\n'
    )

    # gradle.properties
    (project_dir / "gradle.properties").write_text(
        "kotlin.code.style=official\n"
        "android.useAndroidX=true\n"
        "org.gradle.jvmargs=-Xmx2g\n"
    )

    # Shared compose module
    compose_dir = project_dir / "composeApp"
    common_kt = compose_dir / "src" / "commonMain" / "kotlin" / pkg_path
    common_kt.mkdir(parents=True)

    (common_kt / "App.kt").write_text(
        f"package {pkg}\n\n"
        f"import androidx.compose.material3.*\n"
        f"import androidx.compose.runtime.*\n"
        f"import androidx.compose.foundation.layout.*\n"
        f"import androidx.compose.ui.Alignment\n"
        f"import androidx.compose.ui.Modifier\n"
        f"import androidx.compose.ui.unit.dp\n\n"
        f"@Composable\n"
        f"fun App() {{\n"
        f"    MaterialTheme {{\n"
        f"        Surface(\n"
        f"            modifier = Modifier.fillMaxSize(),\n"
        f"            color = MaterialTheme.colorScheme.background\n"
        f"        ) {{\n"
        f"            Column(\n"
        f"                modifier = Modifier.fillMaxSize().padding(24.dp),\n"
        f"                horizontalAlignment = Alignment.CenterHorizontally,\n"
        f"                verticalArrangement = Arrangement.Center\n"
        f"            ) {{\n"
        f"                Text(\n"
        f"                    text = \"{app_name}\",\n"
        f"                    style = MaterialTheme.typography.headlineLarge\n"
        f"                )\n"
        f"            }}\n"
        f"        }}\n"
        f"    }}\n"
        f"}}\n"
    )

    # Android main
    android_kt = compose_dir / "src" / "androidMain" / "kotlin" / pkg_path
    android_kt.mkdir(parents=True)
    (android_kt / "MainActivity.kt").write_text(
        f"package {pkg}\n\n"
        f"import android.os.Bundle\n"
        f"import androidx.activity.ComponentActivity\n"
        f"import androidx.activity.compose.setContent\n\n"
        f"class MainActivity : ComponentActivity() {{\n"
        f"    override fun onCreate(savedInstanceState: Bundle?) {{\n"
        f"        super.onCreate(savedInstanceState)\n"
        f"        setContent {{ App() }}\n"
        f"    }}\n"
        f"}}\n"
    )

    # iOS main
    ios_kt = compose_dir / "src" / "iosMain" / "kotlin" / pkg_path
    ios_kt.mkdir(parents=True)
    (ios_kt / "MainViewController.kt").write_text(
        f"package {pkg}\n\n"
        f"import androidx.compose.ui.window.ComposeUIViewController\n\n"
        f"fun MainViewController() = ComposeUIViewController {{ App() }}\n"
    )

    # WASM main
    wasm_kt = compose_dir / "src" / "wasmJsMain" / "kotlin" / pkg_path
    wasm_kt.mkdir(parents=True)
    (wasm_kt / "Main.kt").write_text(
        f"package {pkg}\n\n"
        f"import androidx.compose.ui.ExperimentalComposeUiApi\n"
        f"import androidx.compose.ui.window.CanvasBasedWindow\n\n"
        f"@OptIn(ExperimentalComposeUiApi::class)\n"
        f"fun main() {{\n"
        f"    CanvasBasedWindow(canvasElementId = \"ComposeTarget\") {{\n"
        f"        App()\n"
        f"    }}\n"
        f"}}\n"
    )

    # Note: build.gradle.kts for composeApp, AndroidManifest.xml,
    # iosApp Xcode project, and gradle/libs.versions.toml would also
    # be needed. For a real setup, use the template approach or
    # the KMP wizard at https://kmp.jetbrains.com
    #
    # Claude Code will fill in the missing pieces when prompted.

    readme = project_dir / "README.md"
    readme.write_text(
        f"# {app_name}\n\n"
        f"Kotlin Multiplatform project targeting Android, iOS, and Web.\n\n"
        f"Built with Compose Multiplatform.\n\n"
        f"## Note\n"
        f"This is a minimal scaffold. Run Claude to build out the full "
        f"gradle config and platform wiring:\n\n"
        f"```\n@{slugify(app_name)} Set up the full KMP build config with "
        f"Android, iOS simulator, and WASM web targets\n```\n"
    )
