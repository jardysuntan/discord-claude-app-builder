"""
supabase_client.py — Async helpers for the Supabase Management API.

Used by /buildapp to auto-provision tables for new apps.
"""

import re
from typing import Optional

import aiohttp

import config

_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def run_sql(sql: str) -> tuple[bool, str]:
    """Execute SQL via the Supabase Management API.

    Returns (success, error_message_or_empty).
    """
    url = f"https://api.supabase.com/v1/projects/{config.SUPABASE_PROJECT_REF}/database/query"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_MANAGEMENT_KEY}",
        "Content-Type": "application/json",
    }
    body = {"query": sql}

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                if resp.status == 200 or resp.status == 201:
                    return (True, "")
                return (False, f"HTTP {resp.status}: {text[:300]}")
    except aiohttp.ClientError as e:
        return (False, f"Request failed: {e}")
    except Exception as e:
        return (False, f"Unexpected error: {e}")


def extract_sql(text: str) -> Optional[str]:
    """Extract SQL from Claude's markdown response (between ```sql and ``` markers)."""
    match = re.search(r"```sql\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None
