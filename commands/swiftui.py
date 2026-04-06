"""
commands/swiftui.py — /swiftui: convert iOS layer from Compose Multiplatform to native SwiftUI.

Adds SKIE to Gradle, builds the Kotlin framework, then uses Claude to translate
all Compose screens into a single SwiftUI ContentView.swift.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable, Optional

from agent_protocol import AgentRunner
from agent_loop import run_agent_loop, format_loop_summary
from helpers.error_reporter import report_error_and_fix
import config


@dataclass
class SwiftUIResult:
    success: bool
    message: str = ""


# ── Step 1: SKIE Gradle setup (idempotent) ─────────────────────────────────

def _add_skie_to_gradle(workspace_path: str) -> list[str]:
    """Add SKIE plugin to Gradle files. Returns list of changes made (empty if already present)."""
    root = Path(workspace_path)
    changes: list[str] = []

    # 1a. gradle/libs.versions.toml — add skie version + plugin
    toml_path = root / "gradle" / "libs.versions.toml"
    if toml_path.exists():
        toml = toml_path.read_text()

        # Add version if not present
        if "skie" not in toml.lower() or 'skie = "' not in toml:
            # Find [versions] section and append
            if "[versions]" in toml:
                toml = toml.replace(
                    "[versions]",
                    '[versions]\nskie = "0.9.5"',
                    1,
                )
                changes.append("Added skie version to libs.versions.toml")

            # Find [plugins] section and append
            if "[plugins]" in toml:
                toml = toml.replace(
                    "[plugins]",
                    '[plugins]\nskie = { id = "co.touchlab.skie", version.ref = "skie" }',
                    1,
                )
                changes.append("Added skie plugin to libs.versions.toml")

            if changes:
                toml_path.write_text(toml)

    # 1b. composeApp/build.gradle.kts — add alias(libs.plugins.skie)
    gradle_path = root / "composeApp" / "build.gradle.kts"
    if gradle_path.exists():
        gradle = gradle_path.read_text()
        if "libs.plugins.skie" not in gradle:
            # Find the plugins { ... } block and append inside it
            # Look for the first line after "plugins {"
            m = re.search(r"(plugins\s*\{)", gradle)
            if m:
                insert_pos = m.end()
                gradle = (
                    gradle[:insert_pos]
                    + "\n    alias(libs.plugins.skie)"
                    + gradle[insert_pos:]
                )
                gradle_path.write_text(gradle)
                changes.append("Added SKIE plugin to composeApp/build.gradle.kts")

    return changes


# ── Step 2: Build Kotlin framework ─────────────────────────────────────────

async def _build_kotlin_framework(workspace_path: str) -> tuple[bool, str]:
    """Build the Kotlin framework with SKIE. Returns (success, output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "./gradlew", ":composeApp:linkDebugFrameworkIosSimulatorArm64",
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        output = stdout.decode(errors="replace")
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        proc.kill()
        return False, "Kotlin framework build timed out (5 min limit)"


# ── Step 3: Claude prompt ──────────────────────────────────────────────────

SWIFTUI_CONVERSION_PROMPT = """\
You are converting an existing KMP/Compose Multiplatform app's iOS layer to native SwiftUI.
The SKIE plugin has already been added to Gradle and the Kotlin framework has been built.

YOUR TASK:
1. Read ALL Compose screens from composeApp/src/commonMain/kotlin/ — look in ui/screens/ \
or any ui/ subdirectory. Read every .kt file there.
2. Read ALL model/data classes from the model/ directory (or data/ directory) in commonMain.
3. Read App.kt (or the main composable entry point) to understand navigation structure \
(tabs, screens, callbacks).
4. Read any ViewModel or Repository classes to understand state management.
5. Write ALL SwiftUI code into iosApp/iosApp/ContentView.swift (single file — \
the Xcode project only knows about this file).

SWIFTUI CONVERSION RULES:

Architecture:
- Create one shared ViewModel (ObservableObject) that observes the app's main StateFlow \
via SKIE. Look for AppDependencies.shared or similar singleton patterns.
- Use withTaskGroup + @MainActor pattern for StateFlow observation:
  ```swift
  await withTaskGroup(of: Void.self) { group in
      group.addTask { @MainActor [weak self] in
          for await value in configFlow {
              self?.config = value
          }
      }
  }
  ```
- TabView root matching the Compose app's bottom navigation
- Each screen as a separate SwiftUI struct
- NavigationStack within each tab

iOS-native patterns:
- SF Symbols (NOT Material Icons) — find appropriate SF Symbol names
- NavigationStack, List, Section, GroupBox for layout
- .sheet for modals, .confirmationDialog for action sheets
- .refreshable for pull-to-refresh
- Use .mint as accent color
- .ultraThinMaterial for glass effects
- Native date/time formatting with Foundation formatters

CRITICAL GOTCHAS — YOU MUST FOLLOW THESE:
- Target iOS 16.0 — NO iOS 17+ APIs. Do NOT use @Observable, .symbolEffect, \
.sensoryFeedback, or any iOS 17+ modifiers. Use @ObservableObject + @Published instead.
- SKIE renames Kotlin `description` property to `description_` in Swift \
(ObjC NSObject collision). Use `event.description_`, `housing.description_`, etc. \
Any Kotlin property named `description` becomes `description_` in Swift.
- StateFlow<Boolean> needs `.boolValue` to become Swift Bool
- Kotlin Int maps to Int32 — wrap with Int() where needed
- Kotlin List<T> maps to Swift [T] natively (no casting needed)
- 6+ TabView tabs auto-collapse to "More" on iOS — this is correct behavior, don't fight it
- Do NOT reference ComposeView or MainViewControllerKt — this is a full SwiftUI app, \
no Compose fallback
- Use @StateObject for view model initialization, not @ObservedObject

File structure:
- Keep the file under 2500 lines. Simplify complex interactive features \
(score entry dropdowns, tournament brackets) rather than writing verbose code.
- Focus on getting the main UI right with real data flowing from Kotlin.
- Include all necessary imports at the top (SwiftUI, ComposeApp)

IMPORTANT CONSTRAINTS:
- Do NOT modify any Kotlin code. Only write Swift.
- Do NOT touch any Gradle files.
- Write ONLY to iosApp/iosApp/ContentView.swift.
- Make sure to `import ComposeApp` at the top of ContentView.swift to access SKIE-bridged Kotlin types.

Now read the Compose source files and generate the SwiftUI equivalent.
"""


# ── Main handler ───────────────────────────────────────────────────────────

async def handle_swiftui(
    workspace_key: str,
    workspace_path: str,
    claude: AgentRunner,
    on_status: Callable[[str], Awaitable[None]],
) -> SwiftUIResult:
    """Convert the iOS layer of a KMP workspace from Compose to SwiftUI."""
    root = Path(workspace_path)

    # Sanity check: must have iosApp/
    if not (root / "iosApp").is_dir():
        return SwiftUIResult(
            success=False,
            message="This workspace has no `iosApp/` directory. "
                    "Only KMP workspaces with an iOS target can be converted.",
        )

    # ── Step 1: Add SKIE to Gradle (idempotent) ────────────────────────────
    await on_status("Step 1/4: Adding SKIE plugin for Kotlin-Swift interop...")
    changes = _add_skie_to_gradle(workspace_path)
    if changes:
        await on_status("  " + "\n  ".join(changes))
    else:
        await on_status("  SKIE already configured — skipping.")

    # ── Step 2: Build Kotlin framework with SKIE ───────────────────────────
    await on_status("Step 2/4: Building Kotlin framework with SKIE (this takes ~60s)...")
    ok, output = await _build_kotlin_framework(workspace_path)
    if not ok:
        # Extract last 800 chars of output for error context
        snippet = output[-800:] if len(output) > 800 else output
        return SwiftUIResult(
            success=False,
            message=(
                "Kotlin framework build failed. SKIE may not be compatible "
                "with this workspace's Kotlin version.\n"
                f"```\n{snippet}\n```"
            ),
        )
    await on_status("  Kotlin framework built with SKIE.")

    # ── Step 3: Claude translates Compose → SwiftUI ────────────────────────
    await on_status("Step 3/4: Claude is reading Compose screens and writing SwiftUI...")

    loop_result = await run_agent_loop(
        initial_prompt=SWIFTUI_CONVERSION_PROMPT,
        workspace_key=workspace_key,
        workspace_path=workspace_path,
        claude=claude,
        platform="ios",
        max_attempts=config.MAX_BUILD_ATTEMPTS,
        on_status=on_status,
    )

    # ── Step 4: Result ─────────────────────────────────────────────────────
    if loop_result.success:
        summary = format_loop_summary(loop_result)
        return SwiftUIResult(
            success=True,
            message=summary,
        )
    else:
        summary = format_loop_summary(loop_result)
        await report_error_and_fix(
            title=f"/swiftui conversion failed ({workspace_key})",
            detail=summary,
            context=f"/swiftui workspace={workspace_key} stage=conversion attempts={loop_result.total_attempts}",
        )
        return SwiftUIResult(
            success=False,
            message=summary,
        )
