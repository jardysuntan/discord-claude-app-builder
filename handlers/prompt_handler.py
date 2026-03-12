"""
handlers/prompt_handler.py — Handle WorkspacePrompt and FallbackPrompt routing.

Extracts the prompt-handling block from bot.py on_message (lines 1754-1898).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import config
from bot_context import STILL_LISTENING
from parser import WorkspacePrompt, FallbackPrompt
from platforms import (
    build_platform,
    WebPlatform,
    iOSPlatform,
    AndroidPlatform,
)
from agent_loop import run_agent_loop, format_loop_summary
from supabase_client import snapshot_sql_files, detect_changed_sql, sync_sql_files
from views.interview_views import CancelRequestView
from helpers.ui_helpers import send_workspace_footer
from helpers.pro_tips import pro_tips_embed, ProTipsDismissView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import ParseResult


async def handle_prompt(
    ctx: BotContext,
    parsed: WorkspacePrompt | FallbackPrompt,
    channel,
    user_id: int,
    is_admin: bool,
) -> None:
    """Route a workspace or fallback prompt to Claude, with cost gating,
    cancel support, SQL sync, auto web build, and auto preview."""

    # If a data-interview is pending for this user, let the interview
    # collector grab the reply instead of routing it to Claude.
    if isinstance(parsed, FallbackPrompt) and (channel.id, user_id) in ctx.interview_pending:
        return

    if isinstance(parsed, WorkspacePrompt):
        ws_key, ws_path = ctx.registry.resolve(parsed.workspace, user_id)
        prompt = parsed.prompt
    else:
        ws_key, ws_path = ctx.registry.resolve(None, user_id)
        prompt = parsed.prompt

    if not ws_key:
        return await ctx.send(channel, "❌ No workspace set. Use `/use <ws>` or `@ws`.")
    if not ws_path:
        return await ctx.send(channel, f"❌ Workspace `{ws_key}` not found.")
    if not ctx.registry.can_access(ws_key, user_id, is_admin, user_email=ctx.allowlist.get_email(user_id)):
        return await ctx.send(channel, "You don't have access to that workspace.")

    # Cost gating
    user_cap = ctx.allowlist.get_daily_cap(user_id)
    if not ctx.cost_tracker.can_afford(user_cap, user_id):
        return await ctx.send(
            channel,
            f"⛔ Daily budget reached (${ctx.cost_tracker.today_spent(user_id):.2f} / "
            f"${user_cap:.2f}). Try again tomorrow.",
        )

    # Maps info
    _MAP_KEYWORDS = {"map", "maps", "google maps", "mapkit", "leaflet", "geolocation", "mapview"}
    if any(kw in prompt.lower() for kw in _MAP_KEYWORDS):
        await ctx.send(channel,
            "\U0001f5fa\ufe0f **Maps:** Leaflet.js maps are fully supported across all platforms. "
            "Google Maps will be supported in a future update."
        )

    cancel_view = CancelRequestView(ctx, user_id, ws_key)
    cancel_msg = await channel.send(
        f"🧠 Thinking in **{ws_key}**…", view=cancel_view,
    )
    await ctx.send(channel, STILL_LISTENING)

    # Snapshot SQL files before Claude runs (for auto-sync)
    sql_before = {}
    if config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
        sql_before = snapshot_sql_files(ws_path)

    async def claude_progress(msg):
        await ctx.send(channel, msg)

    result = await ctx.claude.run(prompt, ws_key, ws_path, on_progress=claude_progress)

    # Remove cancel button now that Claude is done
    try:
        await cancel_msg.edit(
            content=f"🧠 Thinking in **{ws_key}**… done.", view=None,
        )
    except Exception:
        pass

    if cancel_view.cancelled:
        return await ctx.send(channel, "🛑 Request was cancelled.")

    ctx.cost_tracker.add(result.total_cost_usd, user_id)
    if result.exit_code != 0:
        error_detail = result.stderr.strip() or result.stdout.strip() or ""
        # Auto-reset session on context compaction crash so next message works
        if error_detail and "chunk" in error_detail and "limit" in error_detail:
            ctx.claude.clear_session(ws_key)
            return await ctx.send(
                channel,
                "⚠️ Session too large — context compaction crashed.\n"
                "Session has been auto-reset. Please resend your message.",
            )
        # Retry once on transient failures
        if not error_detail or "timeout" in error_detail.lower():
            ctx.claude.clear_session(ws_key)
            await ctx.send(channel, "⚠️ Claude failed, retrying...")
            result = await ctx.claude.run(prompt, ws_key, ws_path, on_progress=claude_progress)
            ctx.cost_tracker.add(result.total_cost_usd, user_id)
            if result.exit_code != 0:
                error_detail = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                return await ctx.send(
                    channel, f"⚠️ Claude failed:\n```\n{error_detail[:1500]}\n```"
                )
        else:
            return await ctx.send(
                channel, f"⚠️ Error:\n```\n{error_detail[:1500]}\n```"
            )
    await ctx.send(channel, result.stdout or "(empty)")

    # Auto-sync changed SQL files to Supabase
    if sql_before and config.SUPABASE_PROJECT_REF and config.SUPABASE_MANAGEMENT_KEY:
        changed_sql = detect_changed_sql(sql_before, ws_path)
        if changed_sql:
            await ctx.send(channel, "🗄️ Updating database...")
            ok, sync_msg = await sync_sql_files(changed_sql)
            icon = "✅" if ok else "⚠️"
            await ctx.send(channel, f"{icon} {sync_msg}")

    # Auto-build web so iPhone users can see updates immediately
    if config.AGENT_MODE:
        await ctx.send(channel, "🌐 Auto-building web...")
        web_result = await build_platform("web", ws_path)
        if web_result.success:
            url = await WebPlatform.serve(ws_path)
            if url:
                await ctx.send(channel, f"✅ Web build succeeded → {url}")
            else:
                await ctx.send(channel, "✅ Web build succeeded (no dist dir found).")
        else:
            await ctx.send(channel, "⚠️ Web build failed — auto-fixing...")

            async def web_fix_status(msg):
                await ctx.send(channel, msg)

            fix_result = await run_agent_loop(
                initial_prompt=(
                    "The wasmJs web build failed. Fix the code so it compiles for web.\n"
                    "Only modify what's necessary for web compatibility.\n\n"
                    f"```\n{web_result.error[:800]}\n```"
                ),
                workspace_key=ws_key,
                workspace_path=ws_path,
                claude=ctx.claude,
                platform="web",
                max_attempts=2,
                on_status=web_fix_status,
            )
            summary = format_loop_summary(fix_result)
            await ctx.send(channel, summary)
            if fix_result.success:
                url = await WebPlatform.serve(ws_path)
                if url:
                    await ctx.send(channel, f"✅ Web fixed → {url}")

        # Auto-preview: build + screenshot for user's preferred platform
        # Non-admin: web preview URL only (no simulator/emulator access)
        user_platform = ctx.registry.get_platform(user_id)
        if is_admin and user_platform in ("ios", "android"):
            await ctx.send(channel, f"📸 Auto-previewing on {user_platform}…")
            preview_build = await build_platform(user_platform, ws_path)
            if preview_build.success:
                try:
                    if user_platform == "ios":
                        await iOSPlatform.install_and_launch(ws_path)
                    else:
                        await AndroidPlatform.install(ws_path)
                        await AndroidPlatform.launch(ws_path)
                    await asyncio.sleep(2)
                    shot = await (
                        iOSPlatform if user_platform == "ios" else AndroidPlatform
                    ).screenshot()
                    if shot:
                        await ctx.send(
                            channel, f"✅ {user_platform.upper()} preview:", file_path=shot
                        )
                    else:
                        await ctx.send(
                            channel, f"⚠️ {user_platform.upper()} screenshot failed."
                        )
                except Exception as e:
                    await ctx.send(
                        channel,
                        f"⚠️ {user_platform.upper()} preview error: {str(e)[:200]}",
                    )
            else:
                await ctx.send(
                    channel,
                    f"⚠️ {user_platform.upper()} build failed — use `/demo` to auto-fix.",
                )

    await send_workspace_footer(ctx, channel, user_id, is_admin=is_admin)

    if ctx.registry.show_tips(user_id):
        view = ProTipsDismissView(ctx, user_id)
        await channel.send(embed=pro_tips_embed(), view=view)
