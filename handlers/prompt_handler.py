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
from views.prompt_suggest_views import PromptSuggestView
from helpers.ui_helpers import send_workspace_footer
from helpers.pro_tips import pro_tip_embed, ProTipsDismissView, TIPS
from helpers.web_screenshot import take_web_screenshot
from helpers.prompt_suggest import suggest as suggest_prompt
from helpers.screenshot_compare import (
    take_app_screenshot,
    build_visual_diff_prompt,
    _guess_route_from_text,
)

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import ParseResult


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".yml", ".yaml",
                    ".kt", ".swift", ".py", ".js", ".ts", ".html", ".css",
                    ".sql", ".toml", ".ini", ".cfg", ".log", ".env.example"}
_MAX_TEXT_SIZE = 50_000  # 50KB max per text file


async def _save_attachments(attachments, ws_path: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Download attachments to workspace.

    Returns (image_paths, text_files) where text_files is [(filename, content)].
    """
    if not attachments:
        return [], []
    from pathlib import Path
    upload_dir = Path(ws_path) / "_discord_uploads"
    upload_dir.mkdir(exist_ok=True)
    images = []
    texts = []
    for att in attachments:
        ext = Path(att.filename).suffix.lower()
        if ext in _IMAGE_EXTENSIONS:
            dest = upload_dir / att.filename
            await att.save(dest)
            images.append(str(dest))
        elif ext in _TEXT_EXTENSIONS or att.content_type and att.content_type.startswith("text/"):
            if att.size and att.size > _MAX_TEXT_SIZE:
                continue
            try:
                raw = await att.read()
                content = raw.decode("utf-8", errors="ignore")
                # Also save to disk so Claude can reference it
                dest = upload_dir / att.filename
                dest.write_text(content)
                texts.append((att.filename, content))
            except Exception:
                continue
    return images, texts


async def handle_prompt(
    ctx: BotContext,
    parsed: WorkspacePrompt | FallbackPrompt,
    channel,
    user_id: int,
    is_admin: bool,
    attachments=None,
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

    # Download attachments and augment prompt
    image_paths, text_files = await _save_attachments(attachments, ws_path)
    if image_paths:
        await ctx.send(channel, f"📎 {len(image_paths)} image(s) attached — capturing current app state…")
        route = _guess_route_from_text(prompt)
        bot_screenshot = await take_app_screenshot(path=route)
        if bot_screenshot:
            await ctx.send(channel, f"📸 Captured current app at `{route}` for comparison.")
        diff_prompt = build_visual_diff_prompt(image_paths, bot_screenshot)
        prompt = f"{diff_prompt}\n\nUser message: {prompt}"
    if text_files:
        file_sections = []
        for fname, content in text_files:
            file_sections.append(f"--- {fname} ---\n{content}")
        prompt = (
            f"The user attached {len(text_files)} file(s). Here are their contents:\n\n"
            + "\n\n".join(file_sections) + f"\n\n{prompt}"
        )
        await ctx.send(channel, f"📎 {len(text_files)} file(s) attached — Claude will read them.")

    # ── Prompt suggestion ────────────────────────────────────────────────
    if config.ENABLE_PROMPT_SUGGESTIONS and not image_paths and not text_files:
        suggestion = await suggest_prompt(prompt)
        if suggestion and suggestion.strip() != prompt.strip():
            view = PromptSuggestView(user_id)
            await channel.send(
                f"💡 **Suggested prompt:**\n> {suggestion}", view=view,
            )
            timed_out = await view.wait()
            if not timed_out and view.choice == "suggested":
                prompt = suggestion
            # If timed out or "original", keep original prompt

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
            url = await WebPlatform.serve(ws_path, ws_key)
            if url:
                await ctx.send(channel, f"✅ Web build succeeded → {url}")
                shot = await take_web_screenshot(f"http://localhost:{config.WEB_SERVE_PORT}")
                if shot:
                    await ctx.send(channel, "📸 Preview:", file_path=shot)
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
                url = await WebPlatform.serve(ws_path, ws_key)
                if url:
                    await ctx.send(channel, f"✅ Web fixed → {url}")
                    shot = await take_web_screenshot(f"http://localhost:{config.WEB_SERVE_PORT}")
                    if shot:
                        await ctx.send(channel, "📸 Preview:", file_path=shot)

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
        tip_index = ctx.registry.get_tip_index(user_id)
        view = ProTipsDismissView(ctx, user_id)
        await channel.send(embed=pro_tip_embed(tip_index), view=view)
        ctx.registry.advance_tip(user_id, total_tips=len(TIPS))
