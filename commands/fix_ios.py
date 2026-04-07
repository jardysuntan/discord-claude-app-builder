"""
commands/fix_ios.py — /fix-ios: make targeted edits to an existing SwiftUI iOS layer.

Unlike /swiftui (which regenerates ContentView.swift from scratch), this command
makes surgical edits to the existing SwiftUI code based on a user-provided description.
"""

from dataclasses import dataclass
from typing import Callable, Awaitable

from agent_protocol import AgentRunner
from agent_loop import run_agent_loop, format_loop_summary
from helpers.error_reporter import report_error_and_fix
import config


@dataclass
class FixIOSResult:
    success: bool
    message: str = ""


FIX_IOS_PROMPT_TEMPLATE = """\
You are making a TARGETED edit to an existing SwiftUI iOS app.

CRITICAL RULES:
- Do NOT rewrite or regenerate ContentView.swift from scratch.
- Read the EXISTING iosApp/iosApp/ContentView.swift first to understand what's there.
- Make the MINIMUM changes needed to accomplish the user's request.
- Preserve all existing functionality, theming, and structure.
- If you need to understand what the KMP/Compose version does, read files in \
composeApp/src/commonMain/kotlin/ for reference — but your edits go ONLY in the SwiftUI file.

SWIFTUI CONSTRAINTS (same as original conversion):
- Target iOS 16.0 — NO iOS 17+ APIs (@Observable, .symbolEffect, .sensoryFeedback, etc.)
- Use @ObservableObject + @Published, NOT @Observable
- SF Symbols for icons (NOT Material Icons)
- SKIE renames Kotlin `description` to `description_` in Swift
- StateFlow<Boolean> needs `.boolValue`
- Kotlin Int maps to Int32 — wrap with Int() where needed
- Write ONLY to iosApp/iosApp/ContentView.swift
- Do NOT modify any Kotlin code or Gradle files
- import ComposeApp and import MapKit must stay at the top

USER REQUEST:
{user_request}

Now read the existing ContentView.swift, understand the current code, and make the requested changes.
"""


async def handle_fix_ios(
    workspace_key: str,
    workspace_path: str,
    claude: AgentRunner,
    on_status: Callable[[str], Awaitable[None]],
    user_request: str,
) -> FixIOSResult:
    """Make targeted edits to an existing SwiftUI iOS layer."""

    prompt = FIX_IOS_PROMPT_TEMPLATE.format(user_request=user_request)

    loop_result = await run_agent_loop(
        initial_prompt=prompt,
        workspace_key=workspace_key,
        workspace_path=workspace_path,
        claude=claude,
        platform="ios",
        max_attempts=config.MAX_BUILD_ATTEMPTS,
        on_status=on_status,
    )

    if loop_result.success:
        summary = format_loop_summary(loop_result)
        return FixIOSResult(success=True, message=summary)
    else:
        summary = format_loop_summary(loop_result)
        await report_error_and_fix(
            title=f"/fix-ios failed ({workspace_key})",
            detail=summary,
            context=f"/fix-ios workspace={workspace_key} request={user_request[:200]}",
        )
        return FixIOSResult(success=False, message=summary)
