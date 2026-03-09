#!/usr/bin/env python3
"""
run_supabase_sql.py — Run SQL against the live Supabase database.

Usage:
    python run_supabase_sql.py "ALTER TABLE guests ADD COLUMN IF NOT EXISTS instagram text"
    python run_supabase_sql.py "UPDATE guests SET instagram = 'handle' WHERE id = 'jared'"
    python run_supabase_sql.py path/to/file.sql

Called by Claude Code during agent sessions to apply database changes directly.
"""

import asyncio
import sys
from pathlib import Path

# Add parent dir to path so we can import config and supabase_client
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import config
from supabase_client import run_sql, check_destructive, patch_idempotent


async def main():
    if len(sys.argv) < 2:
        print("Usage: python run_supabase_sql.py <sql_or_file>", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1]

    # If arg is a file path, read it
    if Path(arg).is_file():
        sql = Path(arg).read_text().strip()
    else:
        sql = arg.strip()

    if not sql:
        print("Error: empty SQL", file=sys.stderr)
        sys.exit(1)

    if not config.SUPABASE_PROJECT_REF or not config.SUPABASE_MANAGEMENT_KEY:
        print("Error: SUPABASE_PROJECT_REF and SUPABASE_MANAGEMENT_KEY must be set", file=sys.stderr)
        sys.exit(1)

    # Safety check
    warnings = check_destructive(sql)
    if warnings:
        print(f"BLOCKED — destructive SQL: {', '.join(warnings)}", file=sys.stderr)
        sys.exit(1)

    # Make idempotent
    sql = patch_idempotent(sql)

    ok, err = await run_sql(sql)
    if ok:
        print("OK")
    else:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
