"""
helpers/google_docs_sync.py — Google Docs → Supabase sync engine.

Reads a Google Doc, uses Claude to parse it into structured data matching
the workspace's DB schema, computes a diff, and applies upserts.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

import config
from supabase_client import query_sql, run_sql
from handlers.data_commands import parse_tables_from_schema


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class TableDiff:
    table: str
    inserts: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)   # each dict: {"old": {...}, "new": {...}}
    deletes: list[dict] = field(default_factory=list)


@dataclass
class SyncPlan:
    diffs: list[TableDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(d.inserts or d.updates or d.deletes for d in self.diffs)

    def summary(self) -> str:
        lines = []
        for d in self.diffs:
            parts = []
            if d.inserts:
                parts.append(f"+{len(d.inserts)} new")
            if d.updates:
                parts.append(f"~{len(d.updates)} updated")
            if d.deletes:
                parts.append(f"-{len(d.deletes)} removed")
            if parts:
                lines.append(f"**{d.table}**: {', '.join(parts)}")
        return "\n".join(lines) if lines else "No changes detected."

    def detail_summary(self) -> str:
        """More detailed summary showing what changed per table."""
        sections = []
        for d in self.diffs:
            lines = [f"**{d.table}**"]
            for row in d.inserts:
                label = _row_label(row)
                lines.append(f"  + {label}")
            for change in d.updates:
                label = _row_label(change["new"])
                changed_fields = _changed_fields(change["old"], change["new"])
                lines.append(f"  ~ {label} ({changed_fields})")
            for row in d.deletes:
                label = _row_label(row)
                lines.append(f"  - {label}")
            if len(lines) > 1:
                sections.append("\n".join(lines))
        return "\n\n".join(sections) if sections else "No changes detected."


def _row_label(row: dict) -> str:
    """Pick a human-readable label for a row."""
    for key in ("name", "title", "label", "email", "description"):
        if key in row and row[key]:
            return str(row[key])
    # Fallback: first non-id, non-null value
    for k, v in row.items():
        if k not in ("id", "created_at", "updated_at") and v is not None:
            return f"{k}={v}"
    return "(row)"


def _changed_fields(old: dict, new: dict) -> str:
    """List which fields changed between old and new."""
    changes = []
    for key, new_val in new.items():
        if key in ("id", "created_at", "updated_at"):
            continue
        old_val = old.get(key)
        if str(new_val) != str(old_val) and not (new_val is None and old_val is None):
            changes.append(key)
    return ", ".join(changes) if changes else "no visible changes"


# ── Google Docs API ───────────────────────────────────────────────────────────


async def read_google_doc(doc_id: str) -> tuple[bool, str]:
    """Fetch doc content as plain text via Google Docs API (service account).

    Returns (ok, text_or_error).
    """
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests
    except ImportError:
        return False, (
            "Missing `google-auth` library. "
            "Install with: `pip install google-auth google-auth-httplib2`"
        )

    if not config.GOOGLE_SERVICE_ACCOUNT_PATH:
        return False, "GOOGLE_SERVICE_ACCOUNT_PATH is not set in .env"

    try:
        creds = service_account.Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_PATH,
            scopes=["https://www.googleapis.com/auth/documents.readonly"],
        )
        # Refresh is synchronous — run in thread
        await asyncio.to_thread(
            creds.refresh, google.auth.transport.requests.Request()
        )
    except Exception as e:
        return False, f"Google auth failed: {e}"

    url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    headers = {"Authorization": f"Bearer {creds.token}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return False, f"Google Docs API error {resp.status}: {text[:200]}"
                doc = await resp.json()
    except aiohttp.ClientError as e:
        return False, f"Request failed: {e}"

    return True, _extract_text(doc)


def _extract_text(document: dict) -> str:
    """Walk the Google Docs JSON body, extract text preserving structure."""
    content = document.get("body", {}).get("content", [])
    lines: list[str] = []

    for element in content:
        if "paragraph" in element:
            paragraph = element["paragraph"]
            style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
            text = ""
            for elem in paragraph.get("elements", []):
                text += elem.get("textRun", {}).get("content", "")
            text = text.rstrip("\n")
            if not text.strip():
                continue
            # Preserve heading structure as markdown
            if "HEADING_1" in style:
                text = f"# {text}"
            elif "HEADING_2" in style:
                text = f"## {text}"
            elif "HEADING_3" in style:
                text = f"### {text}"
            lines.append(text)

        elif "table" in element:
            table = element["table"]
            for row in table.get("tableRows", []):
                cells = []
                for cell in row.get("tableCells", []):
                    cell_text = ""
                    for p in cell.get("content", []):
                        if "paragraph" in p:
                            for e in p["paragraph"].get("elements", []):
                                cell_text += e.get("textRun", {}).get("content", "")
                    cells.append(cell_text.strip())
                lines.append(" | ".join(cells))

    return "\n".join(lines)


# ── Current DB state ─────────────────────────────────────────────────────────


async def fetch_current_state(schema: str, tables: dict) -> dict[str, list[dict]]:
    """Query all syncable tables from Supabase. Returns {table_name: [rows]}."""
    current: dict[str, list[dict]] = {}
    for table_name in tables:
        ok, result = await query_sql(
            f'SELECT * FROM "{table_name}";', schema=schema
        )
        if ok and isinstance(result, list):
            current[table_name] = result
        else:
            current[table_name] = []
    return current


# ── Claude parsing ────────────────────────────────────────────────────────────


async def parse_doc_with_claude(
    doc_text: str, schema_sql: str, current_data: dict
) -> tuple[bool, dict | str]:
    """Call Anthropic API to parse the doc into structured JSON matching schema.

    Returns (ok, parsed_dict_or_error_string).
    """
    current_json = json.dumps(current_data, indent=2, default=str)

    system_prompt = (
        "You are a data extraction assistant. You read a planning document and "
        "extract structured data that matches a database schema.\n\n"
        "Rules:\n"
        "- Output ONLY valid JSON — no markdown fences, no explanation\n"
        "- Match the exact column names from the schema\n"
        "- Preserve existing IDs from current_data when updating existing rows\n"
        "- For new rows, omit the id field (it will be auto-generated)\n"
        "- Use null for unknown/empty optional fields\n"
        "- Return a JSON object where each key is a table name and each value "
        "is an array of row objects\n"
        "- Only include tables that have relevant data in the document\n"
        "- If a table's data hasn't changed from current_data, you may omit it "
        "entirely (this preserves existing data as-is)\n"
        "- Be thorough — extract ALL relevant data from the document, not just "
        "the first few items"
    )

    user_prompt = (
        f"## Database Schema\n{schema_sql}\n\n"
        f"## Current Database State\n{current_json}\n\n"
        f"## Document Content\n{doc_text}\n\n"
        "Extract all structured data from the document into JSON matching "
        "the schema above. Preserve existing IDs where rows match."
    )

    try:
        import anthropic
        client = anthropic.Anthropic()
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()

        # Strip markdown fences if Claude adds them anyway
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)

        parsed = json.loads(text)
        return True, parsed

    except ImportError:
        return False, "Missing `anthropic` library. Install with: `pip install anthropic`"
    except json.JSONDecodeError as e:
        return False, f"Claude returned invalid JSON: {e}"
    except Exception as e:
        return False, f"Claude API error: {e}"


# ── Diff computation ─────────────────────────────────────────────────────────


def compute_diff(parsed: dict, current: dict, tables: dict) -> SyncPlan:
    """Compare Claude's parsed output vs current DB state. Returns a SyncPlan."""
    diffs: list[TableDiff] = []

    for table_name, new_rows in parsed.items():
        if table_name not in tables:
            continue
        if not isinstance(new_rows, list):
            continue

        old_rows = current.get(table_name, [])

        # Build lookup by id
        old_by_id: dict[str, dict] = {}
        for row in old_rows:
            row_id = row.get("id")
            if row_id is not None:
                old_by_id[str(row_id)] = row

        diff = TableDiff(table=table_name)
        seen_ids: set[str] = set()

        for new_row in new_rows:
            row_id = new_row.get("id")
            if row_id is not None and str(row_id) in old_by_id:
                # Potential update
                old_row = old_by_id[str(row_id)]
                seen_ids.add(str(row_id))
                if _rows_differ(old_row, new_row):
                    diff.updates.append({"old": old_row, "new": new_row})
            else:
                # Insert (new row or no matching id)
                diff.inserts.append(new_row)

        # Deletes: old rows not present in new data for this table
        for old_id, old_row in old_by_id.items():
            if old_id not in seen_ids:
                diff.deletes.append(old_row)

        if diff.inserts or diff.updates or diff.deletes:
            diffs.append(diff)

    return SyncPlan(diffs=diffs)


def _rows_differ(old: dict, new: dict) -> bool:
    """Check if two rows differ on any non-auto column."""
    for key, new_val in new.items():
        if key in ("id", "created_at", "updated_at"):
            continue
        old_val = old.get(key)
        if str(new_val) != str(old_val) and not (new_val is None and old_val is None):
            return True
    return False


# ── FK dependency ordering ───────────────────────────────────────────────────


def _parse_fk_order(schema_sql: str, tables: dict) -> list[str]:
    """Parse FK references and return tables in dependency order (parents first)."""
    deps: dict[str, set[str]] = {t: set() for t in tables}

    for m in re.finditer(r"REFERENCES\s+\"?(\w+)\"?", schema_sql, re.IGNORECASE):
        referenced = m.group(1)
        # Find which CREATE TABLE this FK belongs to (last CREATE TABLE before this position)
        pos = m.start()
        table_match = None
        for tm in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?",
            schema_sql[:pos],
            re.IGNORECASE,
        ):
            table_match = tm.group(1)
        if table_match and table_match in deps and referenced in deps:
            deps[table_match].add(referenced)

    # Topological sort — parents before children
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(t: str):
        if t in visited:
            return
        visited.add(t)
        for dep in deps.get(t, set()):
            visit(dep)
        ordered.append(t)

    for t in deps:
        visit(t)

    # Include any tables not in the dependency graph
    for t in tables:
        if t not in visited:
            ordered.append(t)

    return ordered


# ── Execute sync ──────────────────────────────────────────────────────────────


async def execute_sync(
    plan: SyncPlan, schema: str, schema_sql: str, tables: dict
) -> tuple[bool, str]:
    """Apply the SyncPlan to Supabase. Returns (all_ok, status_message)."""
    table_order = _parse_fk_order(schema_sql, tables)
    results: list[str] = []
    all_ok = True

    # Inserts and updates in FK dependency order (parents first)
    for table_name in table_order:
        diff = next((d for d in plan.diffs if d.table == table_name), None)
        if not diff:
            continue

        for row in diff.inserts:
            ok, err = await _upsert_row(table_name, row, schema)
            if not ok:
                results.append(f"Insert {table_name}: {err}")
                all_ok = False

        for change in diff.updates:
            ok, err = await _upsert_row(table_name, change["new"], schema)
            if not ok:
                results.append(f"Update {table_name}: {err}")
                all_ok = False

    # Deletes in reverse FK order (children first)
    for table_name in reversed(table_order):
        diff = next((d for d in plan.diffs if d.table == table_name), None)
        if not diff:
            continue
        for row in diff.deletes:
            row_id = row.get("id")
            if row_id is None:
                continue
            escaped_id = str(row_id).replace("'", "''")
            ok, err = await run_sql(
                f"""DELETE FROM "{table_name}" WHERE id = '{escaped_id}';""",
                schema=schema,
            )
            if not ok:
                results.append(f"Delete {table_name} id={row_id}: {err}")
                all_ok = False

    if not results:
        return True, "All changes applied successfully."

    if all_ok:
        return True, "All changes applied successfully."

    return False, "Some operations failed:\n" + "\n".join(results)


async def _upsert_row(table: str, row: dict, schema: str) -> tuple[bool, str]:
    """INSERT ... ON CONFLICT (id) DO UPDATE for a single row."""
    cols: list[str] = []
    vals: list[str] = []

    for k, v in row.items():
        if k in ("created_at", "updated_at"):
            continue
        cols.append(f'"{k}"')
        if v is None:
            vals.append("NULL")
        elif isinstance(v, bool):
            vals.append("TRUE" if v else "FALSE")
        elif isinstance(v, (int, float)):
            vals.append(str(v))
        else:
            vals.append("'" + str(v).replace("'", "''") + "'")

    col_list = ", ".join(cols)
    val_list = ", ".join(vals)

    # Build update set (exclude id)
    update_parts = []
    for c, v in zip(cols, vals):
        if c != '"id"':
            update_parts.append(f"{c} = {v}")
    update_set = ", ".join(update_parts)

    if update_set:
        sql = (
            f'INSERT INTO "{table}" ({col_list}) VALUES ({val_list}) '
            f"ON CONFLICT (id) DO UPDATE SET {update_set};"
        )
    else:
        sql = (
            f'INSERT INTO "{table}" ({col_list}) VALUES ({val_list}) '
            f"ON CONFLICT (id) DO NOTHING;"
        )

    return await run_sql(sql, schema=schema)


# ── Top-level orchestrator ────────────────────────────────────────────────────


async def sync_google_doc(
    doc_id: str, schema: str, schema_sql: str
) -> tuple[bool, str | SyncPlan]:
    """Full sync pipeline: read doc → parse with Claude → compute diff.

    Returns (ok, SyncPlan) on success or (False, error_string) on failure.
    """
    # 1. Read the Google Doc
    ok, doc_text = await read_google_doc(doc_id)
    if not ok:
        return False, doc_text

    # 2. Parse schema for table info
    tables = parse_tables_from_schema(schema_sql)
    if not tables:
        return False, "No tables found in schema."

    # 3. Fetch current DB state
    current = await fetch_current_state(schema, tables)

    # 4. Parse doc with Claude
    ok, parsed = await parse_doc_with_claude(doc_text, schema_sql, current)
    if not ok:
        return False, parsed

    # 5. Compute diff
    plan = compute_diff(parsed, current, tables)

    return True, plan
