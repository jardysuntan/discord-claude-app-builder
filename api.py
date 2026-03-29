"""
api.py — FastAPI HTTP server for the discord-claude-app-builder.
Runs alongside the Discord bot as a separate process on port 8100.

Start: python api.py
Or:    uvicorn api:app --host 0.0.0.0 --port 8100
"""

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import service

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("api")

# ── Auth ─────────────────────────────────────────────────────────────────────

TOKEN_FILE = Path(__file__).parent / ".api-token"

def _get_api_token() -> str:
    token = os.getenv("API_TOKEN")
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    # Generate and persist a token
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token + "\n")
    TOKEN_FILE.chmod(0o600)
    logger.info(f"Generated API token → {TOKEN_FILE}")
    return token

API_TOKEN = _get_api_token()

async def verify_token(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    if authorization[7:] != API_TOKEN:
        raise HTTPException(403, "Invalid token")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="App Builder API", version="1.0.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    service.init()
    logger.info(f"API ready on port {os.getenv('API_PORT', '8100')}")
    logger.info(f"Token: {API_TOKEN[:8]}...")


# ── Request/Response models ──────────────────────────────────────────────────

class BuildAppRequest(BaseModel):
    description: str
    app_name: str | None = None
    platform: str = "web"
    skip_supabase: bool = False
    webhook_url: str | None = None

class PromptRequestModel(BaseModel):
    prompt: str
    webhook_url: str | None = None

class SaveRequest(BaseModel):
    message: str | None = None

class DemoRequest(BaseModel):
    platform: str = "web"
    webhook_url: str | None = None

class BuildStatusResponse(BaseModel):
    build_id: str
    slug: str
    status: str
    phase: str
    message: str
    platforms: dict = {}
    elapsed_seconds: int = 0
    logs: list[str] = []

class WorkspaceResponse(BaseModel):
    slug: str
    path: str
    platform: str
    owner_id: int | None = None

class RenameRequest(BaseModel):
    new_name: str

class PlatformBuildRequest(BaseModel):
    platform: str = "web"
    webhook_url: str | None = None

class PlanAppRequest(BaseModel):
    description: str

class CommitRequest(BaseModel):
    message: str | None = None
    auto_push: bool = False

class BranchRequest(BaseModel):
    name: str | None = None

class StashRequest(BaseModel):
    pop: bool = False

class GitResponse(BaseModel):
    output: str

class SaveListResponse(BaseModel):
    saves: list[dict] = []
    message: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/api/v1/buildapp", response_model=BuildStatusResponse, dependencies=[Depends(verify_token)])
async def buildapp(req: BuildAppRequest):
    """Start a new app build. Returns immediately with a build_id for polling."""
    build_req = service.BuildRequest(
        description=req.description,
        app_name=req.app_name,
        platform=req.platform,
        skip_supabase=req.skip_supabase,
        webhook_url=req.webhook_url,
    )
    status = await service.build_app(build_req)
    return _status_to_response(status)


@app.get("/api/v1/builds/{build_id}", response_model=BuildStatusResponse, dependencies=[Depends(verify_token)])
async def get_build(build_id: str):
    """Poll build status."""
    status = service.get_build(build_id)
    if not status:
        raise HTTPException(404, f"Build '{build_id}' not found")
    return _status_to_response(status)


@app.get("/api/v1/workspaces", response_model=list[WorkspaceResponse], dependencies=[Depends(verify_token)])
async def list_workspaces():
    workspaces = await service.list_workspaces()
    return [WorkspaceResponse(slug=w.slug, path=w.path, platform=w.platform, owner_id=w.owner_id) for w in workspaces]


@app.get("/api/v1/workspaces/{slug}", response_model=WorkspaceResponse, dependencies=[Depends(verify_token)])
async def get_workspace(slug: str):
    ws = await service.get_workspace(slug)
    if not ws:
        raise HTTPException(404, f"Workspace '{slug}' not found")
    return WorkspaceResponse(slug=ws.slug, path=ws.path, platform=ws.platform, owner_id=ws.owner_id)


@app.post("/api/v1/workspaces/{slug}/prompt", response_model=BuildStatusResponse, dependencies=[Depends(verify_token)])
async def send_prompt(slug: str, req: PromptRequestModel):
    """Send a prompt to a workspace. Returns build_id for polling."""
    try:
        status = await service.send_prompt(service.PromptRequest(workspace=slug, prompt=req.prompt))
        status.webhook_url = req.webhook_url
        return _status_to_response(status)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/demo", response_model=BuildStatusResponse, dependencies=[Depends(verify_token)])
async def demo(slug: str, req: DemoRequest = DemoRequest()):
    """Trigger a demo build for a workspace."""
    try:
        status = await service.demo_workspace(slug, req.platform)
        status.webhook_url = req.webhook_url
        return _status_to_response(status)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/save", dependencies=[Depends(verify_token)])
async def save(slug: str, req: SaveRequest = SaveRequest()):
    """Save/checkpoint a workspace."""
    try:
        return await service.save_workspace(slug, req.message)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Workspace Management ────────────────────────────────────────────────────

@app.post("/api/v1/workspaces/{slug}/use", dependencies=[Depends(verify_token)])
async def set_active_workspace(slug: str, user_id: int = 0):
    """Set workspace as the active default."""
    ok = await service.set_default_workspace(user_id, slug)
    if not ok:
        raise HTTPException(404, f"Workspace '{slug}' not found")
    return {"slug": slug, "active": True}


@app.patch("/api/v1/workspaces/{slug}", dependencies=[Depends(verify_token)])
async def rename_workspace(slug: str, req: RenameRequest):
    """Rename a workspace."""
    ok = await service.rename_workspace(slug, req.new_name)
    if not ok:
        raise HTTPException(400, f"Rename failed — '{slug}' not found or '{req.new_name}' already exists")
    return {"old_slug": slug, "new_slug": req.new_name}


@app.delete("/api/v1/workspaces/{slug}", dependencies=[Depends(verify_token)])
async def delete_workspace(slug: str, force: bool = False):
    """Delete a workspace (registry + files). Protected workspaces require force=true."""
    try:
        ok = await service.delete_workspace(slug, force=force)
    except ValueError as e:
        raise HTTPException(403, str(e))
    if not ok:
        raise HTTPException(404, f"Workspace '{slug}' not found")
    return {"slug": slug, "deleted": True}


@app.post("/api/v1/workspaces/{slug}/protect", dependencies=[Depends(verify_token)])
async def protect_workspace(slug: str, protected: bool = True):
    """Toggle protection on a workspace. Protected workspaces cannot be deleted without force=true."""
    registry = service._get_registry()
    if not registry.exists(slug):
        raise HTTPException(404, f"Workspace '{slug}' not found")
    registry.set_protected(slug, protected)
    return {"slug": slug, "protected": protected}


@app.post("/api/v1/workspaces/{slug}/newsession", dependencies=[Depends(verify_token)])
async def new_session(slug: str):
    """Clear Claude session for a workspace (start fresh conversation)."""
    ws = await service.get_workspace(slug)
    if not ws:
        raise HTTPException(404, f"Workspace '{slug}' not found")
    await service.clear_session(slug)
    return {"slug": slug, "session_cleared": True}


# ── Build & Plan ────────────────────────────────────────────────────────────

@app.post("/api/v1/workspaces/{slug}/build", response_model=BuildStatusResponse, dependencies=[Depends(verify_token)])
async def build_workspace(slug: str, req: PlatformBuildRequest = PlatformBuildRequest()):
    """Build workspace for a specific platform. Returns build_id for polling."""
    try:
        status = await service.build_workspace_platform(slug, req.platform)
        status.webhook_url = req.webhook_url
        return _status_to_response(status)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/planapp", dependencies=[Depends(verify_token)])
async def plan_app(req: PlanAppRequest):
    """Generate an app plan from a description. Synchronous — returns plan JSON."""
    plan = await service.plan_app(req.description)
    if not plan:
        raise HTTPException(500, "Failed to generate plan")
    return plan


@app.post("/api/v1/workspaces/{slug}/appraise", response_model=BuildStatusResponse, dependencies=[Depends(verify_token)])
async def appraise_workspace(slug: str):
    """Run quality appraisal on a workspace. Returns build_id for polling."""
    try:
        status = await service.appraise_workspace(slug)
        return _status_to_response(status)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Git Operations ──────────────────────────────────────────────────────────

@app.get("/api/v1/workspaces/{slug}/git/status", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_status(slug: str):
    """Get git status for a workspace."""
    try:
        output = await service.git_status(slug)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/v1/workspaces/{slug}/git/diff", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_diff(slug: str, full: bool = False):
    """Get git diff. Use ?full=true for full patch."""
    try:
        output = await service.git_diff(slug, full=full)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/v1/workspaces/{slug}/git/log", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_log(slug: str, count: int = 10):
    """Get git log. Use ?count=N to control number of entries."""
    try:
        output = await service.git_log(slug, count=count)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/git/commit", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_commit(slug: str, req: CommitRequest = CommitRequest()):
    """Commit all changes. Omit message for auto-generated commit message."""
    try:
        output = await service.git_commit(slug, message=req.message, auto_push=req.auto_push)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/git/undo", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_undo(slug: str):
    """Revert the last commit."""
    try:
        output = await service.git_undo(slug)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/git/branch", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_branch(slug: str, req: BranchRequest = BranchRequest()):
    """List branches (no body) or create/switch branch (name in body)."""
    try:
        output = await service.git_branch(slug, name=req.name)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/git/stash", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def git_stash(slug: str, req: StashRequest = StashRequest()):
    """Stash changes (default) or pop stash (pop=true)."""
    try:
        output = await service.git_stash(slug, pop=req.pop)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Save System ─────────────────────────────────────────────────────────────

@app.get("/api/v1/workspaces/{slug}/saves", response_model=SaveListResponse, dependencies=[Depends(verify_token)])
async def list_saves(slug: str):
    """List save history for a workspace."""
    try:
        message, saves = await service.save_list(slug)
        return SaveListResponse(saves=saves, message=message)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/workspaces/{slug}/saves/undo", response_model=GitResponse, dependencies=[Depends(verify_token)])
async def undo_save(slug: str):
    """Undo the last save."""
    try:
        output = await service.save_undo(slug)
        return GitResponse(output=output)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Smoke Tests ─────────────────────────────────────────────────────────────

class SmokeTestRequest(BaseModel):
    scenario: str | None = None  # "counter", "map", "video", or None for all
    api_tests: bool = False       # Also run API endpoint checks

@app.post("/api/v1/smoketest", dependencies=[Depends(verify_token)])
async def run_smoketest_endpoint(req: SmokeTestRequest = SmokeTestRequest()):
    """Kick off smoke tests. Returns a build_id for polling status."""
    import uuid
    import time as _time

    build_id = str(uuid.uuid4())[:8]
    status = BuildStatusResponse(
        build_id=build_id,
        slug="smoketest",
        status="running",
        phase="starting",
        message="Smoke test starting...",
    )
    _smoketest_results[build_id] = status

    async def _run():
        from helpers.smoketest_runner import run_smoketest, SCENARIO_NAMES
        from workspaces import WorkspaceRegistry
        from claude_runner import ClaudeRunner

        registry = WorkspaceRegistry()
        claude = ClaudeRunner()
        scenarios = [req.scenario] if req.scenario and req.scenario in SCENARIO_NAMES else None
        logs = []

        async def on_status(msg, file_path=None):
            cleaned = msg.replace("**", "").replace("`", "")
            logs.append(cleaned)
            status.logs = logs[-20:]  # keep last 20
            status.message = cleaned

        try:
            result = await run_smoketest(
                registry=registry,
                claude=claude,
                on_status=on_status,
                is_admin=False,
                scenarios=scenarios,
            )
            status.status = "success" if result.success else "failed"
            status.phase = "complete"
            status.message = result.summary()
            status.logs = logs

            # Run API tests if requested
            if req.api_tests:
                from helpers.api_smoketest import run_api_smoketest
                port = os.getenv("API_PORT", "8100")
                api_result = await run_api_smoketest(
                    base_url=f"http://localhost:{port}",
                    token=API_TOKEN,
                )
                status.message += "\n\n" + api_result.summary()
                if not api_result.success:
                    status.status = "failed"
        except Exception as e:
            logger.exception(f"[smoketest:{build_id}] Failed")
            status.status = "failed"
            status.phase = "complete"
            status.message = f"Error: {e}"

    import asyncio
    asyncio.create_task(_run())
    return status

@app.get("/api/v1/smoketest/{build_id}", dependencies=[Depends(verify_token)])
async def get_smoketest_status(build_id: str):
    """Poll smoke test status."""
    if build_id not in _smoketest_results:
        raise HTTPException(404, f"Smoke test '{build_id}' not found")
    return _smoketest_results[build_id]

_smoketest_results: dict[str, BuildStatusResponse] = {}


# ── Analytics ────────────────────────────────────────────────────────────────

@app.get("/api/v1/analytics", dependencies=[Depends(verify_token)])
async def analytics():
    """Build analytics: success rates, avg durations, per-workspace and per-operation breakdowns."""
    return service.get_analytics()


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health():
    return {"status": "ok"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _status_to_response(s: service.BuildStatus) -> BuildStatusResponse:
    return BuildStatusResponse(
        build_id=s.build_id,
        slug=s.slug,
        status=s.status,
        phase=s.phase,
        message=s.message,
        platforms=s.platforms,
        elapsed_seconds=s.elapsed_seconds,
        logs=s.logs,
    )


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("API_PORT", "8100"))
    logger.info(f"Starting API server on port {port}")
    logger.info(f"API token: {API_TOKEN[:8]}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
