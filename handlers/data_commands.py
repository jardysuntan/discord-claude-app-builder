"""
handlers/data_commands.py — /data export, /data template, /data import.

Lets users download CSV data, get empty CSV templates, and bulk-import rows
without needing access to the Supabase dashboard.
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

import config
from supabase_client import query_sql, run_sql, patch_idempotent

_EXPORT_ROW_LIMIT = 10_000  # max rows per table to avoid huge exports
_EXPORT_QUERY_TIMEOUT = 15  # seconds per table query

if TYPE_CHECKING:
    from bot_context import BotContext
    from parser import Command


# ── Schema parsing ────────────────────────────────────────────────────────────

# Columns that are auto-generated and should be excluded from templates/imports
_AUTO_COLUMNS = {"id", "created_at", "createdat", "updated_at", "updatedat"}


@dataclass
class ColumnInfo:
    name: str
    sql_type: str

    @property
    def friendly_type(self) -> str:
        """Translate SQL type to plain English."""
        t = self.sql_type.upper()
        if "UUID" in t:
            return "unique ID"
        if "SERIAL" in t or "INT" in t:
            return "number"
        if "TEXT" in t or "VARCHAR" in t or "CHAR" in t:
            return "text"
        if "BOOL" in t:
            return "yes/no"
        if "TIMESTAMP" in t:
            return "date & time"
        if "DATE" in t:
            return "date"
        if "TIME" in t:
            return "time"
        if "DOUBLE" in t or "FLOAT" in t or "REAL" in t or "NUMERIC" in t or "DECIMAL" in t:
            return "number"
        if "JSON" in t:
            return "JSON data"
        return t.lower()


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]

    @property
    def col_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def friendly_name(self) -> str:
        """Turn snake_case table name into readable title."""
        return self.name.replace("_", " ").title()

    def describe(self, skip_auto: bool = False) -> str:
        """One-line description with column breakdown."""
        cols = self.columns
        if skip_auto:
            cols = [c for c in cols if c.name.lower() not in _AUTO_COLUMNS]
        parts = [f"`{c.name}` ({c.friendly_type})" for c in cols]
        return ", ".join(parts)


def parse_tables_from_schema(schema_sql: str) -> dict[str, TableInfo]:
    """Return {table_name: TableInfo} from CREATE TABLE statements."""
    tables: dict[str, TableInfo] = {}
    # Match CREATE TABLE [IF NOT EXISTS] <name> ( ... )
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s*\((.*?)\);",
        schema_sql,
        re.IGNORECASE | re.DOTALL,
    ):
        table_name = m.group(1)
        body = m.group(2)
        cols: list[ColumnInfo] = []
        # Split on commas that aren't inside parentheses (handles NUMERIC(10,2) etc.)
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in body:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))

        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Skip constraints (PRIMARY KEY, UNIQUE, FOREIGN KEY, CHECK, CONSTRAINT)
            tokens = part.split()
            first_word = tokens[0].upper().strip('"') if tokens else ""
            if first_word in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
                continue
            # Column name is first token, type is second (may include parens like NUMERIC(10,2))
            col_name = tokens[0].strip('"')
            col_type = tokens[1].strip('"') if len(tokens) > 1 else "TEXT"
            cols.append(ColumnInfo(name=col_name, sql_type=col_type))
        if cols:
            tables[table_name] = TableInfo(name=table_name, columns=cols)
    return tables


def _find_schema_sql(ws_path: str) -> str | None:
    """Find and read schema.sql from the workspace's supabase directory."""
    candidates = [
        os.path.join(ws_path, "supabase", "schema.sql"),
        os.path.join(ws_path, "supabase", "migrations", "schema.sql"),
    ]
    # Also glob for any .sql file with "schema" in the name
    supabase_dir = os.path.join(ws_path, "supabase")
    if os.path.isdir(supabase_dir):
        for root, _, files in os.walk(supabase_dir):
            for f in files:
                if "schema" in f.lower() and f.endswith(".sql"):
                    candidates.append(os.path.join(root, f))

    for path in candidates:
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()
    return None


# ── Handlers ──────────────────────────────────────────────────────────────────


async def handle_data(ctx: BotContext, cmd: Command, channel, user_id: int, is_admin: bool) -> None:
    # Check Supabase is configured
    if not config.SUPABASE_PROJECT_REF or not config.SUPABASE_MANAGEMENT_KEY:
        await ctx.send(channel, "⚠️ No database configured. Ask an admin to set up Supabase.")
        return

    # Resolve workspace
    ws_key, ws_path = ctx.registry.resolve(None, user_id)
    if not ws_key or not ws_path:
        await ctx.send(channel, "⚠️ No workspace selected. Use `/ls` to pick one.")
        return

    sub = cmd.sub
    if sub == "export":
        await _handle_export(ctx, channel, ws_path)
    elif sub == "template":
        await _handle_template(ctx, channel, ws_path)
    elif sub == "import":
        await _handle_import(ctx, cmd, channel, user_id, ws_key, ws_path)
    else:
        await ctx.send(
            channel,
            "**`/data` commands:**\n"
            "`/data export` — download all tables as CSV\n"
            "`/data template` — get empty CSV templates to fill in\n"
            "`/data import` — bulk-import a CSV file",
        )


async def _handle_export(ctx: BotContext, channel, ws_path: str) -> None:
    schema_sql = _find_schema_sql(ws_path)
    if not schema_sql:
        await ctx.send(channel, "⚠️ No `schema.sql` found in this workspace's `supabase/` directory.")
        return

    tables = parse_tables_from_schema(schema_sql)
    if not tables:
        await ctx.send(channel, "⚠️ No tables found in `schema.sql`.")
        return

    total = len(tables)
    await ctx.send(channel, f"📤 Exporting {total} table(s)…")

    files = []
    table_summaries = []
    for i, (table_name, tinfo) in enumerate(tables.items(), 1):
        try:
            ok, result = await asyncio.wait_for(
                query_sql(f'SELECT * FROM "{table_name}" LIMIT {_EXPORT_ROW_LIMIT};'),
                timeout=_EXPORT_QUERY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await ctx.send(channel, f"⏭️ `{table_name}`: timed out, skipping")
            continue

        if not ok:
            await ctx.send(channel, f"⚠️ `{table_name}`: {result}")
            continue

        rows = result
        buf = io.StringIO()
        if rows and isinstance(rows[0], dict):
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.DictWriter(buf, fieldnames=tinfo.col_names)
            writer.writeheader()

        buf.seek(0)
        row_count = len(rows) if isinstance(rows, list) else 0
        files.append(discord.File(io.BytesIO(buf.getvalue().encode()), filename=f"{table_name}.csv"))
        table_summaries.append(f"**{tinfo.friendly_name}** ({row_count} rows) — {tinfo.describe()}")

        # Progress update every 5 tables
        if i % 5 == 0 and i < total:
            await ctx.send(channel, f"⏳ {i}/{total} tables done…")

    if not files:
        await ctx.send(channel, "No data to export.")
        return

    # Send table descriptions
    desc_lines = "\n".join(f"• {s}" for s in table_summaries)
    await ctx.send(channel, f"**Your tables:**\n{desc_lines}")

    # Discord allows max 10 attachments per message — send in batches
    for batch_start in range(0, len(files), 10):
        batch = files[batch_start : batch_start + 10]
        label = f"✅ Exported {len(files)} table(s):" if batch_start == 0 else ""
        await channel.send(content=label or None, files=batch)


async def _handle_template(ctx: BotContext, channel, ws_path: str) -> None:
    schema_sql = _find_schema_sql(ws_path)
    if not schema_sql:
        await ctx.send(channel, "⚠️ No `schema.sql` found in this workspace's `supabase/` directory.")
        return

    tables = parse_tables_from_schema(schema_sql)
    if not tables:
        await ctx.send(channel, "⚠️ No tables found in `schema.sql`.")
        return

    files = []
    table_descs = []
    for table_name, tinfo in tables.items():
        # Skip auto-generated columns
        user_cols = [c for c in tinfo.columns if c.name.lower() not in _AUTO_COLUMNS]
        if not user_cols:
            continue

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([c.name for c in user_cols])
        buf.seek(0)
        files.append(discord.File(io.BytesIO(buf.getvalue().encode()), filename=f"{table_name}.csv"))

        col_desc = ", ".join(f"`{c.name}` ({c.friendly_type})" for c in user_cols)
        table_descs.append(f"**{tinfo.friendly_name}** — {col_desc}")

    if not files:
        await ctx.send(channel, "No tables with user-editable columns found.")
        return

    # Send table descriptions
    desc_lines = "\n".join(f"• {d}" for d in table_descs)
    await ctx.send(
        channel,
        f"**Your tables — fill in these columns:**\n{desc_lines}\n\n"
        "-# Columns like `id` and `created_at` are auto-generated — you don't need to fill those in.",
    )

    # Discord allows max 10 attachments per message — send in batches
    for batch_start in range(0, len(files), 10):
        batch = files[batch_start : batch_start + 10]
        label = "📋 **Templates** — here are your empty CSVs:" if batch_start == 0 else ""
        await channel.send(content=label or None, files=batch)

    # Workflow tip
    await ctx.send(
        channel,
        "**How to fill these in:**\n"
        "1. Open the CSV in **Google Sheets** (or Excel)\n"
        "2. Add your data — one row per item\n"
        "3. **File → Download → CSV** (keep the `.csv` extension)\n"
        "4. Drop the file here and send `/data import`\n\n"
        "-# Tip: keep the filename matching the table name (e.g. `venues.csv` for the venues table).",
    )


async def _handle_import(ctx: BotContext, cmd: Command, channel, user_id: int, ws_key: str, ws_path: str, attachment: discord.Attachment | None = None) -> None:
    if attachment is None:
        # No attachment — prompt for upload
        ctx.awaiting_csv_upload[user_id] = (ws_key, ws_path)
        await ctx.send(channel, "📎 Upload a `.csv` file and I'll import it into your database.\nName the file after the table (e.g. `venues.csv` → `venues` table).")
        return

    await _process_csv_import(ctx, channel, ws_path, attachment)


async def _process_csv_import(ctx: BotContext, channel, ws_path: str, attachment: discord.Attachment) -> None:
    """Read a CSV attachment and insert rows into the matching table."""
    # Infer table name from filename
    filename = attachment.filename
    if not filename.lower().endswith(".csv"):
        await ctx.send(channel, "⚠️ Please upload a `.csv` file.")
        return

    table_name = filename[:-4]  # strip .csv

    # Validate table exists in schema
    schema_sql = _find_schema_sql(ws_path)
    if schema_sql:
        tables = parse_tables_from_schema(schema_sql)
        if table_name not in tables:
            table_list = ", ".join(f"`{t}`" for t in tables)
            await ctx.send(channel, f"⚠️ Table `{table_name}` not found in schema. Available: {table_list}")
            return

    # Download and parse CSV
    csv_bytes = await attachment.read()
    csv_text = csv_bytes.decode("utf-8-sig")  # handle BOM from Excel
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)

    if not rows:
        await ctx.send(channel, "⚠️ CSV is empty (no data rows found).")
        return

    # Build INSERT statements
    columns = list(rows[0].keys())
    col_list = ", ".join(f'"{c}"' for c in columns)
    values_list = []
    for row in rows:
        vals = []
        for c in columns:
            v = row[c]
            if v is None or v == "":
                vals.append("NULL")
            else:
                # Escape single quotes
                vals.append("'" + v.replace("'", "''") + "'")
        values_list.append(f"({', '.join(vals)})")

    # Batch in chunks of 100 rows
    batch_size = 100
    total_imported = 0
    errors = []

    for i in range(0, len(values_list), batch_size):
        batch = values_list[i : i + batch_size]
        sql = f'INSERT INTO "{table_name}" ({col_list}) VALUES\n' + ",\n".join(batch) + ";"
        sql = patch_idempotent(sql)
        ok, err = await run_sql(sql)
        if ok:
            total_imported += len(batch)
        else:
            errors.append(f"Batch {i // batch_size + 1}: {err}")

    if errors:
        error_text = "\n".join(errors)
        await ctx.send(channel, f"⚠️ Imported {total_imported}/{len(rows)} rows into `{table_name}`.\nErrors:\n{error_text}")
    else:
        await ctx.send(channel, f"✅ Imported {total_imported} rows into `{table_name}`.")


# ── Handler map ───────────────────────────────────────────────────────────────

HANDLERS = {
    "data": handle_data,
}
