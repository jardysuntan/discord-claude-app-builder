#!/usr/bin/env python3
"""One-shot script to expose a schema in PostgREST's db_extra_search_path."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers.schema_manager import _expose_schema

SCHEMAS_TO_EXPOSE = [
    "app_yangzihesobachmobile",
    "app_jabluehq",
    "app_photoreviewerkmpbased",
    "app_healthbrain",
]


async def main():
    for schema in SCHEMAS_TO_EXPOSE:
        ok, err = await _expose_schema(schema)
        if ok:
            print(f"  ✅ {schema} exposed")
        else:
            print(f"  ❌ {schema}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
