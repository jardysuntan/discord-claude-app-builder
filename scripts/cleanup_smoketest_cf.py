#!/usr/bin/env python3
"""
One-time script: delete the 10 smoke test Cloudflare Pages projects.

Usage:
    python scripts/cleanup_smoketest_cf.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers.cf_pages import cf_project_name, delete_cf_project

SMOKE_TEST_SLUGS = [
    "smoketest",
    "smoketestcounter",
    "smoketestcounter2",
    "smoketestcounter3",
    "smoketestcounter4",
    "smoketestcounter5",
    "smoketestcounter6",
    "smoketestcounter7",
    "smoketestmap",
    "smoketestvideo",
]


async def main():
    print(f"Deleting {len(SMOKE_TEST_SLUGS)} smoke test CF Pages projects...\n")
    for slug in SMOKE_TEST_SLUGS:
        cf_name = cf_project_name(slug)
        ok = await delete_cf_project(cf_name)
        status = "deleted" if ok else "FAILED"
        print(f"  {cf_name}: {status}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
