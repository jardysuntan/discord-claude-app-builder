"""
asc_api.py — App Store Connect API helpers.

Auto-registers bundle IDs and creates app records so /testflight
works without manual App Store Connect setup.
"""

import asyncio
import time
from pathlib import Path
from typing import Optional, Callable, Awaitable

import jwt
import httpx

import config

ASC_BASE = "https://api.appstoreconnect.apple.com/v1"


def _resolve_key_path() -> Optional[str]:
    """Find the .p8 key file."""
    if config.ASC_KEY_PATH:
        p = Path(config.ASC_KEY_PATH)
        return str(p) if p.exists() else None
    # Standard Apple location
    p = Path.home() / ".private_keys" / f"AuthKey_{config.ASC_KEY_ID}.p8"
    if p.exists():
        return str(p)
    return None


def _generate_jwt() -> str:
    """Generate a short-lived JWT for App Store Connect API."""
    key_path = _resolve_key_path()
    if not key_path:
        raise FileNotFoundError(
            f"ASC .p8 key not found. Expected at ~/.private_keys/AuthKey_{config.ASC_KEY_ID}.p8"
        )
    private_key = Path(key_path).read_text()
    now = int(time.time())
    payload = {
        "iss": config.ASC_ISSUER_ID,
        "iat": now,
        "exp": now + 1200,  # 20 minutes
        "aud": "appstoreconnect-v1",
    }
    return jwt.encode(payload, private_key, algorithm="ES256",
                      headers={"kid": config.ASC_KEY_ID})


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_generate_jwt()}",
        "Content-Type": "application/json",
    }


async def _get(path: str, params: dict = None) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ASC_BASE}{path}", headers=_headers(),
                             params=params, timeout=30)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{ASC_BASE}{path}", headers=_headers(),
                              json=body, timeout=30)
        if r.status_code >= 400:
            detail = r.json().get("errors", [{}])[0].get("detail", r.text[:300])
            raise RuntimeError(f"ASC API error ({r.status_code}): {detail}")
        return r.json()


async def find_bundle_id(identifier: str) -> Optional[str]:
    """Look up a registered bundle ID, return its ASC internal ID or None."""
    data = await _get("/bundleIds", {"filter[identifier]": identifier})
    items = data.get("data", [])
    return items[0]["id"] if items else None


async def register_bundle_id(identifier: str, name: str) -> str:
    """Register a new bundle ID. Returns its ASC internal ID."""
    body = {
        "data": {
            "type": "bundleIds",
            "attributes": {
                "identifier": identifier,
                "name": name,
                "platform": "IOS",
            }
        }
    }
    result = await _post("/bundleIds", body)
    return result["data"]["id"]


async def find_app(bundle_id: str) -> Optional[str]:
    """Look up an app by bundle ID, return its ASC internal ID or None."""
    data = await _get("/apps", {"filter[bundleId]": bundle_id})
    items = data.get("data", [])
    return items[0]["id"] if items else None


async def _patch(path: str, body: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.patch(f"{ASC_BASE}{path}", headers=_headers(),
                               json=body, timeout=30)
        if r.status_code >= 400:
            detail = r.json().get("errors", [{}])[0].get("detail", r.text[:300])
            raise RuntimeError(f"ASC API error ({r.status_code}): {detail}")
        return r.json()


async def update_app_name(bundle_id: str, new_name: str) -> str:
    """Rename an app in App Store Connect. Returns the new name on success."""
    app_id = await find_app(bundle_id)
    if not app_id:
        raise RuntimeError(f"No app found for bundle ID `{bundle_id}`")
    # Get the appInfoLocalization ID for en-US
    data = await _get(f"/apps/{app_id}/appInfos")
    info_id = data["data"][0]["id"]
    loc_data = await _get(f"/appInfos/{info_id}/appInfoLocalizations",
                          {"filter[locale]": "en-US"})
    loc_id = loc_data["data"][0]["id"]
    # Patch the name
    await _patch(f"/appInfoLocalizations/{loc_id}", {
        "data": {
            "type": "appInfoLocalizations",
            "id": loc_id,
            "attributes": {"name": new_name},
        }
    })
    return new_name


async def ensure_app(bundle_id: str, workspace_key: str) -> tuple[bool, str]:
    """
    Ensure the bundle ID is registered and an app record exists.
    Returns (success, message).
    Note: Apple does not allow creating app records via API — only via the web UI.
    """
    app_name = workspace_key.replace("-", " ").replace("_", " ").title()

    try:
        # Step 1: Check if app record exists (most important check)
        app_id = await find_app(bundle_id)
        if app_id:
            return True, f"App `{bundle_id}` found in App Store Connect."

        # Step 2: App not found — try to ensure bundle ID is registered
        # (this can fail with 403 if API key lacks bundleIds scope; that's ok)
        try:
            asc_bundle_id = await find_bundle_id(bundle_id)
            if not asc_bundle_id:
                asc_bundle_id = await register_bundle_id(bundle_id, app_name)
        except Exception:
            pass  # bundle ID registration is best-effort

        return False, f"No app record for `{bundle_id}` in App Store Connect."

    except Exception as e:
        return False, f"Failed to check App Store Connect: {str(e)[:300]}"


async def poll_build_processing(
    bundle_id: str,
    build_number: str,
    on_ready: Callable[[str], Awaitable[None]],
    poll_interval: int = 120,
    timeout: int = 2700,  # 45 minutes
) -> None:
    """Poll App Store Connect until a build finishes processing, then call on_ready."""
    elapsed = 0
    app_id = None

    try:
        app_id = await find_app(bundle_id)
        if not app_id:
            return

        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                data = await _get("/builds", {
                    "filter[app]": app_id,
                    "filter[version]": build_number,
                    "sort": "-uploadedDate",
                    "limit": "1",
                })
                builds = data.get("data", [])
                if not builds:
                    continue

                state = builds[0].get("attributes", {}).get("processingState", "")
                if state == "VALID":
                    await on_ready(
                        f"✅ **{bundle_id}** build `{build_number}` is ready on TestFlight!\n"
                        "Testers with access will get a push notification."
                    )
                    return
                elif state == "FAILED":
                    await on_ready(
                        f"❌ Build `{build_number}` failed Apple processing.\n"
                        "Check App Store Connect for details."
                    )
                    return
                # PROCESSING — keep polling
            except Exception:
                continue  # network blip, try again next cycle

    except Exception:
        pass  # don't crash the bot over a polling failure
