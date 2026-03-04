"""
play_api.py — Google Play Developer Publishing API helpers.

Uses a service account JSON key to authenticate with the
Google Play Developer API v3. Mirrors asc_api.py for the
/playstore flow.
"""

import asyncio
from pathlib import Path
from typing import Callable, Awaitable

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import config

SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]


def _get_service(key_path=None):
    """Build an authenticated androidpublisher service.
    key_path overrides config.PLAY_JSON_KEY_PATH when provided."""
    resolved = Path(key_path or config.PLAY_JSON_KEY_PATH).expanduser()
    if not resolved.exists():
        raise FileNotFoundError(f"Service account key not found: {resolved}")
    creds = service_account.Credentials.from_service_account_file(
        str(resolved), scopes=SCOPES,
    )
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)


async def check_app_exists(package_name: str, key_path=None) -> tuple[bool, str]:
    """Check if the app exists in Google Play by attempting to create an edit.
    Returns (exists, message)."""
    loop = asyncio.get_event_loop()

    def _check():
        service = _get_service(key_path)
        try:
            edit = service.edits().insert(packageName=package_name, body={}).execute()
            # Clean up the edit
            service.edits().delete(packageName=package_name, editId=edit["id"]).execute()
            return True, f"App `{package_name}` found in Google Play Console."
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "applicationNotFound" in error_str:
                return False, (
                    f"App `{package_name}` not found in Google Play Console.\n"
                    "Create it first at https://play.google.com/console"
                )
            if "403" in error_str or "permission" in error_str.lower():
                return False, (
                    f"Permission denied for `{package_name}`. "
                    "Ensure the service account has access in Play Console → "
                    "Setup → API access."
                )
            return False, f"Failed to check Google Play: {error_str[:300]}"

    return await loop.run_in_executor(None, _check)


async def upload_aab(package_name: str, aab_path: str, key_path=None) -> tuple[bool, str, int | None]:
    """Upload an AAB to Google Play internal testing track.
    Returns (success, message, version_code)."""
    loop = asyncio.get_event_loop()

    def _upload():
        service = _get_service(key_path)
        try:
            # Create an edit
            edit = service.edits().insert(packageName=package_name, body={}).execute()
            edit_id = edit["id"]

            # Upload the AAB
            media = MediaFileUpload(aab_path, mimetype="application/octet-stream")
            bundle = service.edits().bundles().upload(
                packageName=package_name,
                editId=edit_id,
                media_body=media,
            ).execute()
            version_code = bundle["versionCode"]

            # Assign to internal testing track and commit
            # Try "completed" first; if app is still draft, retry with "draft" status
            for status in ("completed", "draft"):
                # Re-create edit if retrying (previous edit may be tainted)
                if status == "draft":
                    edit = service.edits().insert(packageName=package_name, body={}).execute()
                    edit_id = edit["id"]
                    bundle = service.edits().bundles().upload(
                        packageName=package_name,
                        editId=edit_id,
                        media_body=media,
                    ).execute()
                    version_code = bundle["versionCode"]

                service.edits().tracks().update(
                    packageName=package_name,
                    editId=edit_id,
                    track="internal",
                    body={
                        "track": "internal",
                        "releases": [{
                            "versionCodes": [str(version_code)],
                            "status": status,
                        }],
                    },
                ).execute()

                try:
                    service.edits().commit(
                        packageName=package_name, editId=edit_id,
                    ).execute()
                    break  # success
                except Exception as e:
                    if "draft" in str(e).lower() and status == "completed":
                        continue  # retry with draft
                    raise

            return True, f"Uploaded version code `{version_code}` to internal testing.", version_code

        except Exception as e:
            error_str = str(e)
            if "apkUpgradeVersionConflict" in error_str or "versionCode" in error_str.lower():
                return False, (
                    "Version code conflict — a build with this version code "
                    "already exists. Try again (uses a new timestamp)."
                ), None
            return False, f"Upload failed: {error_str[:500]}", None

    return await loop.run_in_executor(None, _upload)


async def poll_processing(
    package_name: str,
    version_code: int,
    on_ready: Callable[[str], Awaitable[None]],
    poll_interval: int = 60,
    timeout: int = 600,  # 10 minutes
    key_path=None,
) -> None:
    """Poll Google Play until the build is processed, then call on_ready."""
    elapsed = 0
    loop = asyncio.get_event_loop()

    try:
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                def _check():
                    service = _get_service(key_path)
                    edit = service.edits().insert(packageName=package_name, body={}).execute()
                    edit_id = edit["id"]
                    track = service.edits().tracks().get(
                        packageName=package_name, editId=edit_id, track="internal",
                    ).execute()
                    service.edits().delete(packageName=package_name, editId=edit_id).execute()
                    return track

                track = await loop.run_in_executor(None, _check)
                releases = track.get("releases", [])
                for release in releases:
                    codes = [int(c) for c in release.get("versionCodes", [])]
                    if version_code in codes:
                        status = release.get("status", "")
                        if status == "completed":
                            await on_ready(
                                f"✅ **{package_name}** build `{version_code}` is live on internal testing!\n"
                                "Internal testers will receive the update."
                            )
                            return
            except Exception:
                continue  # network blip, retry

    except Exception:
        pass  # don't crash the bot over polling
