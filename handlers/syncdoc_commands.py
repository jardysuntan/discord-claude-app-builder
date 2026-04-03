"""
handlers/syncdoc_commands.py — /syncdoc command handler.

Syncs a Google Doc with the workspace's Supabase database via Claude parsing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import config
from handlers.data_commands import _find_schema_sql, parse_tables_from_schema
from helpers.google_docs_sync import sync_google_doc
from views.syncdoc_views import SyncConfirmView

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


def _extract_doc_id(raw: str) -> str | None:
    """Extract document ID from a Google Docs URL or raw ID string."""
    raw = raw.strip().strip("<>")  # Discord sometimes wraps URLs in < >
    # Full URL: https://docs.google.com/document/d/DOC_ID/...
    m = re.match(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", raw)
    if m:
        return m.group(1)
    # Already a bare doc ID
    if re.match(r"^[a-zA-Z0-9_-]{10,}$", raw):
        return raw
    return None


async def handle_syncdoc(
    ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool
) -> None:
    # ── Prerequisites ─────────────────────────────────────────────────────
    if not config.GOOGLE_SERVICE_ACCOUNT_PATH:
        await ctx.send(
            channel,
            "\u26a0\ufe0f `GOOGLE_SERVICE_ACCOUNT_PATH` is not set. "
            "Add it to `.env` pointing to your service account JSON key.",
        )
        return

    if not config.SUPABASE_PROJECT_REF or not config.SUPABASE_MANAGEMENT_KEY:
        await ctx.send(channel, "\u26a0\ufe0f No database configured. Ask an admin to set up Supabase.")
        return

    # ── Resolve workspace ─────────────────────────────────────────────────
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_key or not ws_path:
        await ctx.send(channel, "\u26a0\ufe0f No workspace selected. Use `/ls` to pick one.")
        return

    schema = ctx.registry.get_schema(ws_key)
    if not schema:
        await ctx.send(
            channel,
            "\u26a0\ufe0f This workspace has no Supabase schema. "
            "Build the app first so its database is set up.",
        )
        return

    # ── Find schema SQL ───────────────────────────────────────────────────
    schema_sql = _find_schema_sql(ws_path)
    if not schema_sql:
        await ctx.send(
            channel,
            "\u26a0\ufe0f No `schema.sql` found in this workspace's `supabase/` directory.",
        )
        return

    tables = parse_tables_from_schema(schema_sql)
    if not tables:
        await ctx.send(channel, "\u26a0\ufe0f No tables found in the schema.")
        return

    # ── Handle doc URL / ID ───────────────────────────────────────────────
    raw_arg = cmd.raw_cmd
    doc_id = ctx.registry.get_google_doc_id(ws_key)

    if raw_arg:
        new_doc_id = _extract_doc_id(raw_arg)
        if not new_doc_id:
            await ctx.send(
                channel,
                "\u26a0\ufe0f Couldn't parse that as a Google Docs URL. "
                "Use the full URL like: `/syncdoc https://docs.google.com/document/d/.../edit`",
            )
            return
        ctx.registry.set_google_doc_id(ws_key, new_doc_id)
        doc_id = new_doc_id
        await ctx.send(channel, f"\ud83d\udcce Linked Google Doc to **{ws_key}**.")

    if not doc_id:
        await ctx.send(
            channel,
            "\u26a0\ufe0f No Google Doc linked to this workspace yet.\n"
            "Usage: `/syncdoc https://docs.google.com/document/d/YOUR_DOC_ID/edit`",
        )
        return

    # ── Run sync pipeline ─────────────────────────────────────────────────
    await ctx.send(channel, "\ud83d\udd04 Reading Google Doc and analyzing changes\u2026")

    ok, result = await sync_google_doc(doc_id, schema, schema_sql)

    if not ok:
        await ctx.send(channel, f"\u26a0\ufe0f Sync failed: {result}")
        return

    plan = result  # SyncPlan

    if not plan.has_changes:
        await ctx.send(channel, "\u2705 Everything is already in sync \u2014 no changes needed.")
        return

    # ── Show diff and confirmation buttons ────────────────────────────────
    summary = plan.summary()
    detail = plan.detail_summary()

    # Truncate detail if too long for Discord
    if len(detail) > 1500:
        detail = detail[:1500] + "\n\u2026(truncated)"

    msg_text = (
        f"**Sync Preview for {ws_key}**\n\n"
        f"{summary}\n\n"
        f"```\n{detail}\n```\n\n"
        "Click **Confirm** to apply these changes, or **Cancel** to abort."
    )

    async def on_status(text: str):
        await ctx.send(channel, text)

    view = SyncConfirmView(
        plan=plan,
        schema=schema,
        schema_sql=schema_sql,
        tables=tables,
        owner_id=user_id,
        on_status=on_status,
    )

    await channel.send(content=msg_text, view=view)


# ── Handler map ───────────────────────────────────────────────────────────────

HANDLERS = {
    "syncdoc": handle_syncdoc,
}
