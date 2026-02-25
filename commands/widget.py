"""commands/widget.py — /widget for iOS home screen widgets (WidgetKit)."""

from claude_runner import ClaudeRunner
from agent_loop import run_agent_loop, format_loop_summary
from typing import Callable, Awaitable, Optional


async def handle_widget(description, workspace_key, workspace_path, claude,
                        on_status: Callable[[str, Optional[str]], Awaitable[None]] = None):
    if not description:
        prompt = (
            "Customize the iOS widget in iosApp/WidgetExtension/AppWidget.swift. "
            "Create a useful, polished widget that shows relevant app data. "
            "Support .systemSmall and .systemMedium families. "
            "Use pure SwiftUI and WidgetKit only — no Compose in widgets. "
            "You can import the ComposeApp framework if you need shared data."
        )
    else:
        prompt = (
            f"Customize the iOS widget in iosApp/WidgetExtension/AppWidget.swift to: {description}\n\n"
            "Requirements:\n"
            "- Pure SwiftUI + WidgetKit (no Compose in widgets)\n"
            "- Support .systemSmall and .systemMedium families\n"
            "- Use .containerBackground(.fill.tertiary, for: .widget)\n"
            "- You can import ComposeApp framework for shared data if needed\n"
            "- Only modify files in iosApp/WidgetExtension/\n"
            "- Make sure the widget looks great with proper spacing and typography"
        )

    async def loop_status(msg):
        await on_status(msg, None)

    result = await run_agent_loop(
        initial_prompt=prompt, workspace_key=workspace_key,
        workspace_path=workspace_path, claude=claude,
        platform="ios", on_status=loop_status,
    )
    await on_status(format_loop_summary(result), None)
    if result.success:
        await on_status(
            "To see the widget: open iOS simulator \u2192 long-press home screen \u2192 "
            "tap **+** \u2192 search for your app \u2192 add the widget.",
            None,
        )
