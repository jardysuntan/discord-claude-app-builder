"""
helpers/cf_pages.py — Cloudflare Pages name availability checks and suggestions.

Used at app-creation time to detect globally-taken pages.dev names
before we reach the deploy step.
"""

import re
import random
from typing import Tuple

import httpx

import config


def cf_project_name(slug: str) -> str:
    """Sanitize a slug into a valid CF Pages project name.

    Mirrors the logic in platforms.py WebPlatform.deploy().
    """
    return re.sub(r"[^a-z0-9-]", "-", slug.lower()).strip("-")


async def check_cf_name_available(project_name: str) -> str:
    """Check whether a CF Pages project name is available.

    Returns:
        "ours"      — we already own a project with this name (safe to redeploy)
        "taken"     — someone else owns it on pages.dev
        "available" — name appears free
    """
    if not config.CLOUDFLARE_API_TOKEN or not config.CLOUDFLARE_ACCOUNT_ID:
        return "available"

    headers = {
        "Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}",
    }
    acct = config.CLOUDFLARE_ACCOUNT_ID

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: check if WE own a project with this name
            resp = await client.get(
                f"https://api.cloudflare.com/client/v4/accounts/{acct}/pages/projects/{project_name}",
                headers=headers,
            )
            if resp.status_code == 200:
                return "ours"

            # Step 2: probe the public pages.dev domain
            try:
                probe = await client.head(
                    f"https://{project_name}.pages.dev",
                    follow_redirects=True,
                )
                if probe.status_code < 500:
                    return "taken"
            except (httpx.ConnectError, httpx.TimeoutException):
                # DNS didn't resolve or timed out — name is likely free
                pass

    except Exception:
        # Network issue — don't block app creation
        pass

    return "available"


async def delete_cf_project(project_name: str) -> bool:
    """Delete a Cloudflare Pages project. Returns True on success or if already gone (404)."""
    if not config.CLOUDFLARE_API_TOKEN or not config.CLOUDFLARE_ACCOUNT_ID:
        return False

    headers = {
        "Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}",
    }
    acct = config.CLOUDFLARE_ACCOUNT_ID

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"https://api.cloudflare.com/client/v4/accounts/{acct}/pages/projects/{project_name}",
                headers=headers,
            )
            return resp.status_code in (200, 404)
    except Exception:
        return False


def generate_alternatives(base_name: str, count: int = 3) -> list[str]:
    """Return deterministic + random alternative names."""
    alts = []
    # Numeric suffixes
    for i in range(2, 2 + count):
        alts.append(f"{base_name}{i}")
    # "my-" prefix
    alts.append(f"my-{base_name}")
    # Random 3-digit suffix
    alts.append(f"{base_name}-{random.randint(100, 999)}")
    return alts[:count]


async def find_available_name(base_slug: str) -> Tuple[str, str]:
    """Try the base name, then alternatives, and return the first available.

    Returns:
        (app_name, cf_project_name) tuple
    """
    cf_name = cf_project_name(base_slug)
    status = await check_cf_name_available(cf_name)
    if status != "taken":
        return base_slug, cf_name

    for alt in generate_alternatives(base_slug, count=5):
        alt_cf = cf_project_name(alt)
        alt_status = await check_cf_name_available(alt_cf)
        if alt_status != "taken":
            return alt, alt_cf

    # Fallback: timestamp suffix (virtually guaranteed unique)
    import time
    ts = str(int(time.time()))[-6:]
    fallback = f"{base_slug}-{ts}"
    return fallback, cf_project_name(fallback)
