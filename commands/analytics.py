"""commands/analytics.py — /analytics command: app metrics from TestFlight, Play Store, and build health."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from commands.build_log import get_builds, get_all_builds
from platforms import iOSPlatform, AndroidPlatform

if TYPE_CHECKING:
    from workspaces import WorkspaceRegistry
    from cost_tracker import CostTracker

logger = logging.getLogger("commands.analytics")


# ── Sparkline helper ────────────────────────────────────────────────────────

def _sparkline(values: list[float]) -> str:
    """Render a list of numbers as an ASCII sparkline."""
    if not values:
        return ""
    bars = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    spread = hi - lo if hi != lo else 1
    return "".join(bars[min(8, int((v - lo) / spread * 8))] for v in values)


# ── TestFlight metrics ──────────────────────────────────────────────────────

async def _fetch_testflight_metrics(bundle_id: str) -> Optional[dict]:
    """Fetch TestFlight build and beta tester info from App Store Connect."""
    try:
        from asc_api import find_app, _get
    except ImportError:
        return None

    try:
        app_id = await find_app(bundle_id)
        if not app_id:
            return None

        # Fetch recent builds
        data = await _get("/builds", {
            "filter[app]": app_id,
            "sort": "-uploadedDate",
            "limit": "5",
        })
        builds = data.get("data", [])
        if not builds:
            return {"app_id": app_id, "builds": 0, "latest_state": "none"}

        latest = builds[0].get("attributes", {})
        latest_state = latest.get("processingState", "unknown")
        latest_version = latest.get("version", "?")
        latest_build = latest.get("uploadedDate", "")[:10]

        # Fetch beta testers count
        tester_count = 0
        try:
            tester_data = await _get(f"/apps/{app_id}/betaTesters", {"limit": "1"})
            # Use meta.paging.total if available, else count returned
            meta = tester_data.get("meta", {}).get("paging", {})
            tester_count = meta.get("total", len(tester_data.get("data", [])))
        except Exception:
            pass

        # Fetch beta feedback count
        feedback_count = 0
        try:
            feedback_data = await _get(f"/apps/{app_id}/betaAppReviewDetails")
            feedback_count = len(feedback_data.get("data", []))
        except Exception:
            pass

        return {
            "app_id": app_id,
            "builds": len(builds),
            "latest_state": latest_state,
            "latest_version": latest_version,
            "latest_upload": latest_build,
            "tester_count": tester_count,
            "feedback_count": feedback_count,
        }
    except Exception as e:
        logger.warning(f"TestFlight metrics fetch failed: {e}")
        return None


# ── Play Store metrics ──────────────────────────────────────────────────────

async def _fetch_play_metrics(package_name: str, key_path: str = None) -> Optional[dict]:
    """Fetch Play Store internal track info."""
    try:
        from play_api import _get_service
    except ImportError:
        return None

    loop = asyncio.get_event_loop()

    def _fetch():
        try:
            service = _get_service(key_path)
            edit = service.edits().insert(packageName=package_name, body={}).execute()
            edit_id = edit["id"]

            # Get internal track info
            track = service.edits().tracks().get(
                packageName=package_name, editId=edit_id, track="internal",
            ).execute()

            # Get all tracks for overview
            tracks = service.edits().tracks().list(
                packageName=package_name, editId=edit_id,
            ).execute()

            service.edits().delete(packageName=package_name, editId=edit_id).execute()

            releases = track.get("releases", [])
            active_release = None
            for r in releases:
                if r.get("status") in ("completed", "inProgress"):
                    active_release = r
                    break

            track_names = [t.get("track", "?") for t in tracks.get("tracks", [])]

            return {
                "package": package_name,
                "internal_releases": len(releases),
                "active_version": (
                    active_release.get("versionCodes", ["?"])[0]
                    if active_release else None
                ),
                "active_status": active_release.get("status") if active_release else "none",
                "tracks": track_names,
            }
        except Exception as e:
            logger.warning(f"Play Store metrics fetch failed: {e}")
            return None

    return await loop.run_in_executor(None, _fetch)


# ── Build health from local logs ────────────────────────────────────────────

def _build_health_metrics(ws_path: str) -> dict:
    """Compute build health metrics from workspace build log."""
    all_builds = get_all_builds(ws_path)
    if not all_builds:
        return {"total": 0}

    total = len(all_builds)
    successes = sum(1 for b in all_builds if b["success"])
    failures = total - successes
    total_attempts = sum(b.get("attempts", 1) for b in all_builds)
    durations = [b["duration_secs"] for b in all_builds if b.get("duration_secs")]
    costs = [b.get("cost_usd", 0) for b in all_builds]

    # Per-platform breakdown
    by_platform: dict[str, dict] = {}
    for b in all_builds:
        plat = b["platform"].lower()
        entry = by_platform.setdefault(plat, {"total": 0, "pass": 0, "fail": 0})
        entry["total"] += 1
        if b["success"]:
            entry["pass"] += 1
        else:
            entry["fail"] += 1

    # Recent trend (last 10 builds as pass/fail sparkline)
    recent = all_builds[-10:]
    trend_values = [1.0 if b["success"] else 0.0 for b in recent]

    return {
        "total": total,
        "successes": successes,
        "failures": failures,
        "pass_rate": round(successes / total * 100, 1) if total else 0,
        "total_attempts": total_attempts,
        "avg_duration": round(sum(durations) / len(durations), 1) if durations else 0,
        "total_cost": round(sum(costs), 4),
        "by_platform": by_platform,
        "trend": _sparkline(trend_values),
        "last_build": all_builds[-1] if all_builds else None,
    }


# ── Format the analytics embed ─────────────────────────────────────────────

PLATFORM_ICONS = {"ios": "🍎", "android": "🤖", "web": "🌐"}


def _format_analytics(
    ws_key: str,
    build_health: dict,
    testflight: Optional[dict] = None,
    play_store: Optional[dict] = None,
) -> str:
    """Format all analytics into a Discord-friendly text embed."""
    sections: list[str] = []
    sections.append(f"📊 **Analytics — {ws_key}**\n")

    # ── TestFlight ──────────────────────────────────────────────────────
    sections.append("**🍎 TestFlight**")
    if testflight:
        state_icon = "✅" if testflight["latest_state"] == "VALID" else "⏳"
        sections.append(f"  Latest build: {state_icon} v{testflight['latest_version']} ({testflight['latest_state'].lower()})")
        sections.append(f"  Uploaded: {testflight['latest_upload']}")
        sections.append(f"  Builds: {testflight['builds']}  ·  Testers: {testflight['tester_count']}")
        if testflight.get("feedback_count"):
            sections.append(f"  Beta feedback: {testflight['feedback_count']}")
    else:
        sections.append("  Not configured or no builds yet")

    # ── Play Store ──────────────────────────────────────────────────────
    sections.append("\n**🤖 Play Store (Internal)**")
    if play_store:
        status_icon = "✅" if play_store["active_status"] == "completed" else "⏳"
        sections.append(f"  Status: {status_icon} {play_store['active_status']}")
        if play_store["active_version"]:
            sections.append(f"  Active version code: `{play_store['active_version']}`")
        sections.append(f"  Releases: {play_store['internal_releases']}")
        if play_store["tracks"]:
            sections.append(f"  Tracks: {', '.join(play_store['tracks'])}")
    else:
        sections.append("  Not configured or no releases yet")

    # ── Build Health ────────────────────────────────────────────────────
    sections.append("\n**🔨 Build Health**")
    if build_health["total"] == 0:
        sections.append("  No builds yet")
    else:
        sections.append(f"  Pass rate: **{build_health['pass_rate']}%** ({build_health['successes']}/{build_health['total']})")
        if build_health.get("trend"):
            sections.append(f"  Trend: {build_health['trend']}")
        sections.append(f"  Avg duration: {_format_duration(build_health['avg_duration'])}")
        sections.append(f"  Total attempts: {build_health['total_attempts']}  ·  Cost: ${build_health['total_cost']:.2f}")

        # Per-platform breakdown
        for plat in ("ios", "android", "web"):
            info = build_health["by_platform"].get(plat)
            if info:
                icon = PLATFORM_ICONS.get(plat, "📦")
                rate = round(info["pass"] / info["total"] * 100) if info["total"] else 0
                sections.append(f"  {icon} {plat.capitalize()}: {rate}% pass ({info['pass']}/{info['total']})")

        # Last build
        last = build_health.get("last_build")
        if last:
            status = "✅" if last["success"] else "❌"
            ts = last["ts"][5:16].replace("T", " ")
            sections.append(f"\n  Last build: {status} {last['platform']} — {ts}")

    return "\n".join(sections)


def _format_duration(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s" if m else f"{s}s"


# ── Main entry point ────────────────────────────────────────────────────────

async def run_analytics(
    ws_key: str,
    ws_path: str,
    registry: WorkspaceRegistry,
) -> str:
    """Fetch all analytics for a workspace and return formatted text."""
    import config

    # Determine app identifiers
    bundle_id = iOSPlatform.parse_bundle_id(ws_path)
    package_name = AndroidPlatform.parse_app_id(ws_path)

    # Build health is always available (local data)
    build_health = _build_health_metrics(ws_path)

    # Fetch remote metrics concurrently
    tf_task = None
    play_task = None

    has_testflight = bool(config.APPLE_TEAM_ID and config.ASC_KEY_ID and config.ASC_ISSUER_ID)
    has_play = bool(config.PLAY_JSON_KEY_PATH)

    if bundle_id and has_testflight:
        tf_task = asyncio.create_task(_fetch_testflight_metrics(bundle_id))
    if package_name and has_play:
        play_task = asyncio.create_task(_fetch_play_metrics(package_name))

    testflight = await tf_task if tf_task else None
    play_store = await play_task if play_task else None

    return _format_analytics(ws_key, build_health, testflight, play_store)
