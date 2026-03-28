"""
service.py — Stable contract layer for the HTTP API.
Wraps existing build logic from commands/buildapp.py, agent_loop.py, etc.
"""

import asyncio
import os
import shutil
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from workspaces import WorkspaceRegistry
from claude_runner import ClaudeRunner
from commands.buildapp import handle_buildapp
from commands import git_cmd
from commands.planapp import generate_plan
from commands.appraise import run_appraisal
from agent_loop import run_agent_loop, format_loop_summary
from platforms import build_platform, demo_platform, WebPlatform

logger = logging.getLogger("api.service")

# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class BuildRequest:
    description: str
    app_name: str | None = None
    platform: str = "web"
    skip_supabase: bool = False
    webhook_url: str | None = None

@dataclass
class BuildStatus:
    build_id: str
    slug: str
    status: str  # queued, building, success, failed
    phase: str   # scaffolding, schema, building, fixing, demoing
    message: str
    platforms: dict = field(default_factory=dict)
    elapsed_seconds: int = 0
    logs: list[str] = field(default_factory=list)
    webhook_url: str | None = None  # fires on completion

@dataclass
class PromptRequest:
    workspace: str
    prompt: str

@dataclass
class WorkspaceInfo:
    slug: str
    path: str
    platform: str
    owner_id: int | None


# ── Singleton state ──────────────────────────────────────────────────────────

_registry: WorkspaceRegistry | None = None
_claude: ClaudeRunner | None = None
_builds: dict[str, BuildStatus] = {}

# ── Analytics tracking ───────────────────────────────────────────────────────

_analytics: dict = {
    "total_builds": 0,
    "successes": 0,
    "failures": 0,
    "total_duration_secs": 0,
    "by_operation": {},  # buildapp, prompt, demo, build, appraise
    "by_workspace": {},  # slug -> {total, successes, failures}
}


def _track_build(operation: str, slug: str, success: bool, duration_secs: int):
    """Record a completed build for analytics."""
    _analytics["total_builds"] += 1
    _analytics["total_duration_secs"] += duration_secs
    if success:
        _analytics["successes"] += 1
    else:
        _analytics["failures"] += 1

    op = _analytics["by_operation"].setdefault(operation, {"total": 0, "successes": 0, "failures": 0, "total_duration": 0})
    op["total"] += 1
    op["total_duration"] += duration_secs
    if success:
        op["successes"] += 1
    else:
        op["failures"] += 1

    ws = _analytics["by_workspace"].setdefault(slug, {"total": 0, "successes": 0, "failures": 0})
    ws["total"] += 1
    if success:
        ws["successes"] += 1
    else:
        ws["failures"] += 1


def get_analytics() -> dict:
    """Return build analytics summary."""
    total = _analytics["total_builds"]
    return {
        **_analytics,
        "success_rate": round(_analytics["successes"] / total * 100, 1) if total else 0,
        "avg_duration_secs": round(_analytics["total_duration_secs"] / total) if total else 0,
    }


async def _fire_webhook(status: BuildStatus):
    """Fire webhook if configured on this build."""
    if not status.webhook_url:
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(status.webhook_url, json={
                "build_id": status.build_id,
                "status": status.status,
                "slug": status.slug,
                "phase": status.phase,
                "message": status.message,
                "elapsed_seconds": status.elapsed_seconds,
                "platforms": status.platforms,
            }, timeout=10)
    except Exception as e:
        logger.warning(f"Webhook failed for {status.build_id}: {e}")


def init(registry: WorkspaceRegistry | None = None, claude: ClaudeRunner | None = None):
    """Initialize the service layer with shared instances."""
    global _registry, _claude
    _registry = registry or WorkspaceRegistry()
    _claude = claude or ClaudeRunner()


def _get_registry() -> WorkspaceRegistry:
    global _registry
    if _registry is None:
        _registry = WorkspaceRegistry()
    return _registry


def _get_claude() -> ClaudeRunner:
    global _claude
    if _claude is None:
        _claude = ClaudeRunner()
    return _claude


# ── Build tracking ───────────────────────────────────────────────────────────

def get_build(build_id: str) -> BuildStatus | None:
    return _builds.get(build_id)


def list_builds() -> list[BuildStatus]:
    return list(_builds.values())


# ── Service functions ────────────────────────────────────────────────────────

async def build_app(request: BuildRequest) -> BuildStatus:
    """Start a build in the background. Returns immediately with a build_id."""
    build_id = str(uuid.uuid4())[:8]
    status = BuildStatus(
        build_id=build_id,
        slug="",
        status="queued",
        phase="scaffolding",
        message="Build queued",
    )
    _builds[build_id] = status

    async def _run_build():
        registry = _get_registry()
        claude = _get_claude()
        start = time.time()

        async def on_status(msg: str, _attachment: str | None = None):
            status.message = msg
            status.elapsed_seconds = int(time.time() - start)
            status.logs.append(msg)
            # Infer phase from message content
            if "🏗️" in msg or "Creating" in msg:
                status.phase = "scaffolding"
            elif "🗄️" in msg or "schema" in msg.lower():
                status.phase = "schema"
            elif "🧠" in msg or "Claude" in msg:
                status.phase = "building"
            elif "⚠️" in msg or "fix" in msg.lower():
                status.phase = "fixing"
            elif "🌐" in msg or "📱" in msg or "demo" in msg.lower():
                status.phase = "demoing"
            logger.info(f"[build:{build_id}] {msg[:120]}")

        status.status = "building"

        try:
            slug = await handle_buildapp(
                description=request.description,
                registry=registry,
                claude=claude,
                on_status=on_status,
                on_ask=None,  # no interactive questions via API
                is_admin=True,
                owner_id=None,
                app_name=request.app_name,
            )
            status.slug = slug or ""
            status.elapsed_seconds = int(time.time() - start)

            if slug:
                status.status = "success"
                status.phase = "complete"
            else:
                status.status = "failed"
                status.phase = "complete"
        except Exception as e:
            logger.exception(f"[build:{build_id}] Build failed with exception")
            status.status = "failed"
            status.phase = "complete"
            status.message = f"Build error: {e}"
            status.elapsed_seconds = int(time.time() - start)

        _track_build("buildapp", status.slug or "unknown", status.status == "success", status.elapsed_seconds)
        await _fire_webhook(status)

    asyncio.create_task(_run_build())
    return status


async def send_prompt(request: PromptRequest) -> BuildStatus:
    """Send a prompt to an existing workspace. Runs in background."""
    registry = _get_registry()
    claude = _get_claude()
    
    ws_path = registry.get_path(request.workspace)
    if not ws_path:
        raise ValueError(f"Workspace '{request.workspace}' not found")

    build_id = str(uuid.uuid4())[:8]
    status = BuildStatus(
        build_id=build_id,
        slug=request.workspace,
        status="building",
        phase="building",
        message="Sending prompt to Claude...",
    )
    _builds[build_id] = status

    async def _run():
        start = time.time()
        try:
            async def on_status(msg: str):
                status.message = msg
                status.elapsed_seconds = int(time.time() - start)
                status.logs.append(msg)

            loop_result = await run_agent_loop(
                initial_prompt=request.prompt,
                workspace_key=request.workspace,
                workspace_path=ws_path,
                claude=claude,
                platform="web",
                on_status=on_status,
            )
            status.status = "success" if loop_result.success else "failed"
            status.phase = "complete"
            status.message = format_loop_summary(loop_result)
            status.elapsed_seconds = int(time.time() - start)
        except Exception as e:
            logger.exception(f"[prompt:{build_id}] Failed")
            status.status = "failed"
            status.phase = "complete"
            status.message = f"Error: {e}"

        _track_build("prompt", slug, status.status == "success", status.elapsed_seconds)
        await _fire_webhook(status)

    asyncio.create_task(_run())
    return status


async def list_workspaces() -> list[WorkspaceInfo]:
    registry = _get_registry()
    result = []
    for key in registry.list_keys():
        path = registry.get_path(key)
        owner = registry.get_owner(key)
        result.append(WorkspaceInfo(
            slug=key,
            path=path or "",
            platform="kmp",
            owner_id=owner,
        ))
    return result


async def get_workspace(slug: str) -> WorkspaceInfo | None:
    registry = _get_registry()
    path = registry.get_path(slug)
    if not path:
        return None
    return WorkspaceInfo(
        slug=slug,
        path=path,
        platform="kmp",
        owner_id=registry.get_owner(slug),
    )


async def demo_workspace(slug: str, platform: str = "web") -> BuildStatus:
    """Trigger a demo build for a workspace. Returns build status."""
    registry = _get_registry()
    ws_path = registry.get_path(slug)
    if not ws_path:
        raise ValueError(f"Workspace '{slug}' not found")

    build_id = str(uuid.uuid4())[:8]
    status = BuildStatus(
        build_id=build_id,
        slug=slug,
        status="building",
        phase="demoing",
        message=f"Starting {platform} demo...",
    )
    _builds[build_id] = status

    async def _run():
        start = time.time()
        try:
            result = await demo_platform(platform, ws_path, workspace_key=slug)
            status.status = "success" if result.success else "failed"
            status.message = result.message
            status.phase = "complete"
            if result.demo_url:
                status.platforms[platform] = {"success": True, "url": result.demo_url}
            status.elapsed_seconds = int(time.time() - start)
        except Exception as e:
            logger.exception(f"[demo:{build_id}] Failed")
            status.status = "failed"
            status.phase = "complete"
            status.message = f"Error: {e}"

        _track_build("demo", slug, status.status == "success", status.elapsed_seconds)
        await _fire_webhook(status)

    asyncio.create_task(_run())
    return status


async def set_default_workspace(user_id: int, slug: str) -> bool:
    """Set a workspace as the active default for a user."""
    registry = _get_registry()
    return registry.set_default(user_id, slug)


async def rename_workspace(slug: str, new_name: str) -> bool:
    """Rename a workspace. Returns False if slug not found or new_name taken."""
    registry = _get_registry()
    return registry.rename(slug, new_name)


async def delete_workspace(slug: str) -> bool:
    """Delete a workspace from registry and disk. Returns False if not found."""
    registry = _get_registry()
    ws_path = registry.get_path(slug)
    if not ws_path:
        return False
    registry.remove(slug)
    if os.path.isdir(ws_path):
        shutil.rmtree(ws_path, ignore_errors=True)
    return True


async def clear_session(slug: str) -> None:
    """Clear the Claude session for a workspace (fresh conversation)."""
    claude = _get_claude()
    claude.clear_session(slug)


async def build_workspace_platform(slug: str, platform: str = "web") -> BuildStatus:
    """Build a workspace for a specific platform. Returns build status for polling."""
    registry = _get_registry()
    ws_path = registry.get_path(slug)
    if not ws_path:
        raise ValueError(f"Workspace '{slug}' not found")

    build_id = str(uuid.uuid4())[:8]
    status = BuildStatus(
        build_id=build_id,
        slug=slug,
        status="building",
        phase="building",
        message=f"Building {platform}...",
    )
    _builds[build_id] = status

    async def _run():
        start = time.time()
        try:
            result = await build_platform(platform, ws_path)
            status.status = "success" if result.success else "failed"
            status.message = result.output[:500] if result.success else result.error[:500]
            status.phase = "complete"
            if result.success:
                status.platforms[platform] = {"success": True}
            status.elapsed_seconds = int(time.time() - start)
        except Exception as e:
            logger.exception(f"[build-platform:{build_id}] Failed")
            status.status = "failed"
            status.phase = "complete"
            status.message = f"Error: {e}"

        _track_build("build", slug, status.status == "success", status.elapsed_seconds)
        await _fire_webhook(status)

    asyncio.create_task(_run())
    return status


async def plan_app(description: str) -> dict | None:
    """Generate an app plan synchronously. Returns plan dict or None."""
    claude = _get_claude()
    return await generate_plan(description, claude)


async def appraise_workspace(slug: str) -> BuildStatus:
    """Run appraisal on a workspace. Background task with polling."""
    registry = _get_registry()
    ws_path = registry.get_path(slug)
    if not ws_path:
        raise ValueError(f"Workspace '{slug}' not found")

    claude = _get_claude()
    build_id = str(uuid.uuid4())[:8]
    status = BuildStatus(
        build_id=build_id,
        slug=slug,
        status="building",
        phase="building",
        message="Running appraisal...",
    )
    _builds[build_id] = status

    async def _run():
        start = time.time()
        try:
            result = await run_appraisal(claude, slug, ws_path)
            status.status = "success" if result else "failed"
            status.phase = "complete"
            if result:
                status.message = result.get("overall_summary", "Appraisal complete")
                status.platforms = {"appraisal": result}
            else:
                status.message = "Appraisal returned no results"
            status.elapsed_seconds = int(time.time() - start)
        except Exception as e:
            logger.exception(f"[appraise:{build_id}] Failed")
            status.status = "failed"
            status.phase = "complete"
            status.message = f"Error: {e}"

        _track_build("appraise", slug, status.status == "success", status.elapsed_seconds)
        await _fire_webhook(status)

    asyncio.create_task(_run())
    return status


# ── Git operation wrappers ──────────────────────────────────────────────────

def _resolve_workspace(slug: str) -> tuple[str, str]:
    """Resolve slug to (ws_key, ws_path). Raises ValueError if not found."""
    registry = _get_registry()
    ws_path = registry.get_path(slug)
    if not ws_path:
        raise ValueError(f"Workspace '{slug}' not found")
    return slug, ws_path


async def git_status(slug: str) -> str:
    ws_key, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_status(ws_path, ws_key)


async def git_diff(slug: str, full: bool = False) -> str:
    _, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_diff(ws_path, full=full)


async def git_log(slug: str, count: int = 10) -> str:
    _, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_log(ws_path, count=count)


async def git_commit(slug: str, message: str | None = None, auto_push: bool = False) -> str:
    ws_key, ws_path = _resolve_workspace(slug)
    claude = _get_claude()
    return await git_cmd.handle_commit(ws_path, ws_key, message=message, claude=claude, auto_push=auto_push)


async def git_undo(slug: str) -> str:
    _, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_undo(ws_path)


async def git_branch(slug: str, name: str | None = None) -> str:
    _, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_branch(ws_path, name=name)


async def git_stash(slug: str, pop: bool = False) -> str:
    _, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_stash(ws_path, pop=pop)


async def save_list(slug: str) -> tuple[str, list]:
    _, ws_path = _resolve_workspace(slug)
    message, saves = await git_cmd.handle_save_list(ws_path)
    saves_dicts = [{"num": num, "description": desc, "date": date} for num, desc, date in saves]
    return message, saves_dicts


async def save_undo(slug: str) -> str:
    _, ws_path = _resolve_workspace(slug)
    return await git_cmd.handle_save_undo(ws_path)


async def save_workspace(slug: str, message: str | None = None) -> dict:
    """Save/checkpoint a workspace via git commit."""
    registry = _get_registry()
    ws_path = registry.get_path(slug)
    if not ws_path:
        raise ValueError(f"Workspace '{slug}' not found")

    # Ensure git repo exists (workspaces created via API may not have one)
    from commands.git_cmd import ensure_git_repo
    ok, git_msg = await ensure_git_repo(ws_path)
    if not ok:
        return {"slug": slug, "saved": False, "message": f"Git init failed: {git_msg}"}

    proc = await asyncio.create_subprocess_exec(
        "git", "add", "-A",
        cwd=ws_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    commit_msg = message or f"API checkpoint: {slug}"
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", commit_msg, "--allow-empty",
        cwd=ws_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()

    return {
        "slug": slug,
        "saved": proc.returncode == 0,
        "message": (out or err or b"").decode(errors="replace")[:500],
    }
