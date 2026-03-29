#!/usr/bin/env python3
"""
One-time script: backfill app_name + description columns into existing app_meta tables,
then re-create the get_all_apps() RPC so the JablueHQ dashboard can discover apps.

Usage:
    python scripts/backfill_app_meta.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase_client import run_sql, query_sql
from helpers.schema_manager import list_schemas, ensure_dashboard_function


# Map schema names to display metadata — only the 4 core apps for now
APP_METADATA = {
    "app_jabluehq": {
        "app_name": "JablueHQ",
        "description": "Command center dashboard for Jablue projects",
    },
    "app_yangzihesobachmobile": {
        "app_name": "Yangzihe Sobach Mobile",
        "description": "Mobile companion app for Yangzihe Sobach",
    },
    "app_novalifepuppy": {
        "app_name": "Nova Life Puppy",
        "description": "Puppy care, vet, and training tracker for Nova",
    },
    "app_photoreviewerkmpbased": {
        "app_name": "Photo Reviewer",
        "description": "Photo organizer and deleter app",
    },
}


async def main():
    schemas = await list_schemas()
    print(f"Found {len(schemas)} app schemas: {schemas}")

    if not schemas:
        print("No app_* schemas found. Nothing to backfill.")
        return

    for schema in schemas:
        print(f"\n--- {schema} ---")

        # 1. Add columns if missing
        alter_sql = f"""
ALTER TABLE {schema}.app_meta ADD COLUMN IF NOT EXISTS app_name text;
ALTER TABLE {schema}.app_meta ADD COLUMN IF NOT EXISTS description text;
"""
        ok, err = await run_sql(alter_sql)
        if ok:
            print(f"  Columns ensured.")
        else:
            print(f"  ALTER failed (table may not exist): {err[:200]}")
            continue

        # 2. Upsert metadata if we have it
        meta = APP_METADATA.get(schema)
        if meta:
            safe_name = meta["app_name"].replace("'", "''")
            safe_desc = meta["description"].replace("'", "''")
            upsert_sql = (
                f"INSERT INTO {schema}.app_meta (id, app_name, description) "
                f"VALUES (1, '{safe_name}', '{safe_desc}') "
                f"ON CONFLICT (id) DO UPDATE SET "
                f"app_name = EXCLUDED.app_name, description = EXCLUDED.description;"
            )
            ok, err = await run_sql(upsert_sql)
            if ok:
                print(f"  Metadata set: {meta['app_name']}")
            else:
                print(f"  Upsert failed: {err[:200]}")
        else:
            print(f"  No metadata mapping for {schema}, skipping upsert.")

    # 3. Re-create the dashboard function with the app_name filter
    print("\n--- Updating get_all_apps() RPC ---")
    ok, err = await ensure_dashboard_function()
    if ok:
        print("  RPC updated successfully.")
    else:
        print(f"  RPC update failed: {err[:200]}")

    # 4. Test the RPC
    print("\n--- Testing get_all_apps() ---")
    ok, data = await query_sql("SELECT public.get_all_apps() as result;")
    if ok and data:
        print(f"  Result: {data}")
    else:
        print(f"  Test failed: {data}")


if __name__ == "__main__":
    asyncio.run(main())
