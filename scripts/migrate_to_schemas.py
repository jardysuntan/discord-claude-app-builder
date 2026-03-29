#!/usr/bin/env python3
"""
One-time migration: move all app tables from public schema to per-app schemas.

For each app:
  1. CREATE SCHEMA app_<name>
  2. GRANT permissions to anon/authenticated
  3. ALTER TABLE public.<table> SET SCHEMA app_<name>  (moves data + indexes + RLS)
  4. CREATE app_meta in the new schema with display metadata
  5. Update workspaces.json with supabase_schema

Usage:
    python3 scripts/migrate_to_schemas.py
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase_client import run_sql, query_sql
from helpers.schema_manager import ensure_schema, ensure_dashboard_function

WORKSPACES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "workspaces.json",
)

# ── App → table mapping ──────────────────────────────────────────────────────

APPS = {
    "jabluehq": {
        "schema": "app_jabluehq",
        "tables": ["campaigns", "todos", "completed"],
        "meta": {
            "app_name": "JablueHQ",
            "description": "Command center dashboard for Jablue projects",
        },
    },
    "yangzihesobachmobile": {
        "schema": "app_yangzihesobachmobile",
        "tables": [
            "golf_scores", "golf_rounds", "golf_tees",
            "drink_counts", "contests", "weight_entries",
            "superlatives", "confessions", "expenses",
            "guests", "rsvps", "events", "venues",
            "updates", "decisions", "housings",
            "rps_tournaments",
        ],
        "meta": {
            "app_name": "Yangzihe Sobach Mobile",
            "description": "Mobile companion app for Yangzihe Sobach",
        },
    },
    "novalifepuppy": {
        "schema": "app_novalifepuppy",
        "tables": [
            "puppies", "training_logs", "happiness_logs",
            "activities", "tricks", "milestones",
        ],
        "meta": {
            "app_name": "Nova Life Puppy",
            "description": "Puppy care, vet, and training tracker for Nova",
        },
    },
    "photoreviewerkmpbased": {
        "schema": "app_photoreviewerkmpbased",
        "tables": ["photos", "deletion_queue", "user_settings", "photo_stats"],
        "meta": {
            "app_name": "Photo Reviewer",
            "description": "Photo organizer and deleter app",
        },
    },
}

# Tables that exist in public but don't belong to these 4 apps — leave them alone.
# app_meta is special: we leave public.app_meta and create fresh ones per schema.


async def table_exists(schema: str, table: str) -> bool:
    ok, data = await query_sql(
        f"SELECT 1 FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}';"
    )
    if not ok or not data:
        return False
    rows = data[0] if isinstance(data[0], list) else data
    return len(rows) > 0


async def migrate_app(slug: str, app_config: dict):
    schema = app_config["schema"]
    tables = app_config["tables"]
    meta = app_config["meta"]

    print(f"\n{'='*60}")
    print(f"Migrating: {slug} → {schema}")
    print(f"{'='*60}")

    # 1. Create schema with grants
    print(f"  Creating schema {schema}...")
    ok, err = await ensure_schema(schema)
    if not ok:
        print(f"  FAILED to create schema: {err[:200]}")
        return False
    print(f"  Schema created.")

    # 2. Move tables
    moved = 0
    skipped = 0
    for table in tables:
        # Check if table exists in public
        exists_in_public = await table_exists("public", table)
        if not exists_in_public:
            print(f"  SKIP {table} — not in public schema")
            skipped += 1
            continue

        # Check if already moved
        exists_in_target = await table_exists(schema, table)
        if exists_in_target:
            print(f"  SKIP {table} — already in {schema}")
            skipped += 1
            continue

        print(f"  Moving {table}...")
        ok, err = await run_sql(f"ALTER TABLE public.{table} SET SCHEMA {schema};")
        if ok:
            print(f"  Moved {table} ✓")
            moved += 1
        else:
            print(f"  FAILED to move {table}: {err[:200]}")
            return False

    print(f"  Tables: {moved} moved, {skipped} skipped")

    # 3. Grant permissions on moved tables
    ok, err = await run_sql(
        f"GRANT ALL ON ALL TABLES IN SCHEMA {schema} TO anon, authenticated;"
    )
    if not ok:
        print(f"  WARNING: grant failed: {err[:200]}")

    # 4. Create app_meta in the new schema
    print(f"  Creating app_meta in {schema}...")
    app_meta_sql = f"""
CREATE TABLE IF NOT EXISTS {schema}.app_meta (
    id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    app_name text,
    description text,
    version text,
    "lastUpdated" timestamptz DEFAULT now(),
    "joinCode" text,
    "organizerCode" text,
    "createdAt" timestamptz DEFAULT now()
);
ALTER TABLE {schema}.app_meta ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "public_access" ON {schema}.app_meta FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""
    ok, err = await run_sql(app_meta_sql)
    if not ok:
        print(f"  FAILED to create app_meta: {err[:200]}")
        return False

    # 5. Upsert metadata
    safe_name = meta["app_name"].replace("'", "''")
    safe_desc = meta["description"].replace("'", "''")
    ok, err = await run_sql(
        f"INSERT INTO {schema}.app_meta (id, app_name, description) "
        f"VALUES (1, '{safe_name}', '{safe_desc}') "
        f"ON CONFLICT (id) DO UPDATE SET "
        f"app_name = EXCLUDED.app_name, description = EXCLUDED.description;"
    )
    if ok:
        print(f"  Metadata set: {meta['app_name']} ✓")
    else:
        print(f"  FAILED to upsert metadata: {err[:200]}")
        return False

    return True


async def main():
    print("Schema Migration: public → per-app schemas")
    print("=" * 60)

    # Pre-flight: check what's in public
    ok, data = await query_sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"
    )
    if ok:
        rows = data[0] if isinstance(data[0], list) else data
        public_tables = [r["table_name"] for r in rows]
        print(f"Tables in public before migration: {len(public_tables)}")
    else:
        print("WARNING: Could not list public tables")
        public_tables = []

    # Migrate each app
    results = {}
    for slug, app_config in APPS.items():
        ok = await migrate_app(slug, app_config)
        results[slug] = ok

    # Summary
    print(f"\n{'='*60}")
    print("Migration Results:")
    print(f"{'='*60}")
    all_ok = True
    for slug, ok in results.items():
        status = "SUCCESS" if ok else "FAILED"
        print(f"  {slug}: {status}")
        if not ok:
            all_ok = False

    if not all_ok:
        print("\nSome migrations failed. Fix errors and re-run (script is idempotent).")
        return

    # Update workspaces.json
    print(f"\nUpdating workspaces.json...")
    with open(WORKSPACES_PATH) as f:
        workspaces = json.load(f)

    for slug, app_config in APPS.items():
        if slug in workspaces:
            workspaces[slug]["supabase_schema"] = app_config["schema"]
            print(f"  {slug} → {app_config['schema']}")

    with open(WORKSPACES_PATH, "w") as f:
        json.dump(workspaces, f, indent=2)
    print("  workspaces.json updated ✓")

    # Re-deploy get_all_apps() RPC
    print(f"\nUpdating get_all_apps() RPC...")
    ok, err = await ensure_dashboard_function()
    if ok:
        print("  RPC updated ✓")
    else:
        print(f"  RPC update failed: {err[:200]}")

    # Test
    print(f"\nTesting get_all_apps()...")
    ok, data = await query_sql("SELECT public.get_all_apps() as result;")
    if ok and data:
        rows = data[0] if isinstance(data[0], list) else data
        print(f"  Result: {rows[0] if rows else 'empty'}")
    else:
        print(f"  Test failed: {data}")

    # Show what's left in public
    ok, data = await query_sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"
    )
    if ok:
        rows = data[0] if isinstance(data[0], list) else data
        remaining = [r["table_name"] for r in rows]
        print(f"\nTables remaining in public: {len(remaining)}")
        for t in remaining:
            print(f"  {t}")


if __name__ == "__main__":
    asyncio.run(main())
