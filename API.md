# App Builder HTTP API v1

HTTP API for triggering builds programmatically. Runs on port **8100** (configurable via `API_PORT`).

## Auth

All endpoints (except `/health`) require a Bearer token in the `Authorization` header.

Token is read from `API_TOKEN` env var. If not set, a random token is generated and written to `.api-token`.

```
Authorization: Bearer <token>
```

## Endpoints

### Build & Create

#### POST /api/v1/buildapp

Start a new app build from a description. Returns immediately with a `build_id` for polling.

```bash
curl -X POST http://localhost:8100/api/v1/buildapp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "a todo list app", "app_name": "TodoList"}'
```

**Body:**
- `description` (required) — what to build
- `app_name` (optional) — defaults to inferred name
- `platform` — `web` (default), `android`, `ios`, `all`
- `skip_supabase` — skip database setup (default false)
- `webhook_url` — URL to POST when build completes

**Response:** `BuildStatus`

#### POST /api/v1/planapp

Generate a structured app plan from a description. **Synchronous** — returns plan JSON directly.

```bash
curl -X POST http://localhost:8100/api/v1/planapp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "a workout tracker with sets and reps"}'
```

**Body:**
- `description` (required) — what the app should do

**Response:** Plan JSON with `app_name`, `summary`, `screens`, `navigation`, `data_model`, `features`, `tech_decisions`

### Build Status

#### GET /api/v1/builds/{build_id}

Poll build/task status. Works for all async operations (buildapp, prompt, demo, build, appraise).

```bash
curl http://localhost:8100/api/v1/builds/abc123 \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "build_id": "abc123",
  "slug": "todolist",
  "status": "building",
  "phase": "building",
  "message": "🧠 Sending prompt to Claude...",
  "platforms": {},
  "elapsed_seconds": 45,
  "logs": ["🏗️ Creating TodoList...", "..."]
}
```

Status values: `queued`, `building`, `success`, `failed`
Phase values: `scaffolding`, `schema`, `building`, `fixing`, `demoing`, `complete`

### Workspaces

#### GET /api/v1/workspaces

List all workspaces.

```bash
curl http://localhost:8100/api/v1/workspaces \
  -H "Authorization: Bearer $TOKEN"
```

#### GET /api/v1/workspaces/{slug}

Get workspace details.

```bash
curl http://localhost:8100/api/v1/workspaces/todolist \
  -H "Authorization: Bearer $TOKEN"
```

#### POST /api/v1/workspaces/{slug}/use

Set workspace as the active default.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/use \
  -H "Authorization: Bearer $TOKEN"
```

#### PATCH /api/v1/workspaces/{slug}

Rename a workspace.

```bash
curl -X PATCH http://localhost:8100/api/v1/workspaces/todolist \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"new_name": "my-todo-app"}'
```

#### DELETE /api/v1/workspaces/{slug}

Delete a workspace (removes from registry and deletes files).

```bash
curl -X DELETE http://localhost:8100/api/v1/workspaces/todolist \
  -H "Authorization: Bearer $TOKEN"
```

#### POST /api/v1/workspaces/{slug}/newsession

Clear Claude's conversation session for a workspace (start fresh).

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/newsession \
  -H "Authorization: Bearer $TOKEN"
```

### Prompt & Build

#### POST /api/v1/workspaces/{slug}/prompt

Send a prompt to an existing workspace (add features, fix bugs). Returns `build_id` for polling.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/prompt \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add a dark mode toggle"}'
```

#### POST /api/v1/workspaces/{slug}/build

Build workspace for a specific platform. Returns `build_id` for polling.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/build \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"platform": "web"}'
```

**Body:**
- `platform` — `web` (default), `android`, `ios`

#### POST /api/v1/workspaces/{slug}/demo

Trigger a demo build. Returns `build_id` for polling.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/demo \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"platform": "web"}'
```

#### POST /api/v1/workspaces/{slug}/appraise

Run a quality appraisal (deterministic checks + Claude completeness scan). Returns `build_id` for polling. Appraisal results are in `platforms.appraisal` of the build status.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/appraise \
  -H "Authorization: Bearer $TOKEN"
```

### Git Operations

All git endpoints return `{"output": "..."}` with the command output.

#### GET /api/v1/workspaces/{slug}/git/status

```bash
curl http://localhost:8100/api/v1/workspaces/todolist/git/status \
  -H "Authorization: Bearer $TOKEN"
```

#### GET /api/v1/workspaces/{slug}/git/diff

```bash
# Summary (default)
curl http://localhost:8100/api/v1/workspaces/todolist/git/diff \
  -H "Authorization: Bearer $TOKEN"

# Full patch
curl "http://localhost:8100/api/v1/workspaces/todolist/git/diff?full=true" \
  -H "Authorization: Bearer $TOKEN"
```

#### GET /api/v1/workspaces/{slug}/git/log

```bash
curl "http://localhost:8100/api/v1/workspaces/todolist/git/log?count=5" \
  -H "Authorization: Bearer $TOKEN"
```

#### POST /api/v1/workspaces/{slug}/git/commit

Commit all changes. Omit `message` for auto-generated commit message via Claude.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/git/commit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "feat: add dark mode"}'
```

**Body:**
- `message` (optional) — commit message; auto-generated if omitted
- `auto_push` — push after commit (default false)

#### POST /api/v1/workspaces/{slug}/git/undo

Revert the last commit.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/git/undo \
  -H "Authorization: Bearer $TOKEN"
```

#### POST /api/v1/workspaces/{slug}/git/branch

List branches (empty body) or create/switch branch.

```bash
# List branches
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/git/branch \
  -H "Authorization: Bearer $TOKEN"

# Create/switch
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/git/branch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "feature/dark-mode"}'
```

#### POST /api/v1/workspaces/{slug}/git/stash

Stash or pop stashed changes.

```bash
# Stash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/git/stash \
  -H "Authorization: Bearer $TOKEN"

# Pop
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/git/stash \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pop": true}'
```

### Save System

#### POST /api/v1/workspaces/{slug}/save

Save/checkpoint a workspace (game-save-style versioning with auto-description).

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/save \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Added dark mode"}'
```

#### GET /api/v1/workspaces/{slug}/saves

List save history.

```bash
curl http://localhost:8100/api/v1/workspaces/todolist/saves \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "saves": [
    {"num": 3, "description": "added dark mode toggle", "date": "2026-03-26T10:30:00+00:00"},
    {"num": 2, "description": "fixed login button", "date": "2026-03-25T15:00:00+00:00"},
    {"num": 1, "description": "todo list with categories", "date": "2026-03-25T12:00:00+00:00"}
  ],
  "message": "📋 **Save History**\n..."
}
```

#### POST /api/v1/workspaces/{slug}/saves/undo

Undo the last save.

```bash
curl -X POST http://localhost:8100/api/v1/workspaces/todolist/saves/undo \
  -H "Authorization: Bearer $TOKEN"
```

### Health

#### GET /api/v1/health

Health check (no auth required).

```bash
curl http://localhost:8100/api/v1/health
```

## Running

```bash
# Direct
python api.py

# Or with uvicorn
uvicorn api:app --host 0.0.0.0 --port 8100

# Or via PM2
pm2 start ecosystem.config.cjs --only app-builder-api
```

## Interactive docs

Swagger UI available at `http://localhost:8100/api/docs` (no auth required for docs page).

---

## Jablue Integration Guide

### Full Workflow Example

```
planapp → buildapp → prompt (iterate) → build → demo → save
```

1. **Plan** the app (optional): `POST /planapp` → get structured plan
2. **Build** from description: `POST /buildapp` → get `build_id`, poll until `status=success`
3. **Iterate** with prompts: `POST /workspaces/{slug}/prompt` → poll until done
4. **Build** for platform: `POST /workspaces/{slug}/build` → compile for web/android/ios
5. **Demo**: `POST /workspaces/{slug}/demo` → get demo URL
6. **Save**: `POST /workspaces/{slug}/save` → checkpoint progress
7. **Appraise** (optional): `POST /workspaces/{slug}/appraise` → quality check

### Python Example with httpx

```python
import httpx
import asyncio

BASE = "http://localhost:8100/api/v1"
HEADERS = {"Authorization": "Bearer YOUR_TOKEN"}

async def build_and_iterate():
    async with httpx.AsyncClient(timeout=300) as client:
        # 1. Build a new app
        r = await client.post(f"{BASE}/buildapp", headers=HEADERS, json={
            "description": "a workout tracker with exercises, sets, and reps",
            "app_name": "FitTrack",
        })
        build = r.json()
        build_id = build["build_id"]

        # 2. Poll until complete
        while True:
            r = await client.get(f"{BASE}/builds/{build_id}", headers=HEADERS)
            status = r.json()
            if status["status"] in ("success", "failed"):
                break
            await asyncio.sleep(5)

        slug = status["slug"]

        # 3. Send a follow-up prompt
        r = await client.post(f"{BASE}/workspaces/{slug}/prompt", headers=HEADERS, json={
            "prompt": "Add a rest timer between sets with a countdown UI",
        })
        prompt_id = r.json()["build_id"]

        # Poll prompt completion
        while True:
            r = await client.get(f"{BASE}/builds/{prompt_id}", headers=HEADERS)
            if r.json()["status"] in ("success", "failed"):
                break
            await asyncio.sleep(5)

        # 4. Save progress
        await client.post(f"{BASE}/workspaces/{slug}/save", headers=HEADERS, json={
            "message": "Added rest timer",
        })

        # 5. Check git status
        r = await client.get(f"{BASE}/workspaces/{slug}/git/status", headers=HEADERS)
        print(r.json()["output"])

asyncio.run(build_and_iterate())
```

### Polling Pattern

All async operations (buildapp, prompt, demo, build, appraise) return a `build_id`. Poll `GET /builds/{build_id}` until `status` is `success` or `failed`:

```python
async def wait_for_build(client, build_id, interval=5, timeout=600):
    import time
    start = time.time()
    while time.time() - start < timeout:
        r = await client.get(f"{BASE}/builds/{build_id}", headers=HEADERS)
        data = r.json()
        if data["status"] in ("success", "failed"):
            return data
        await asyncio.sleep(interval)
    raise TimeoutError(f"Build {build_id} timed out")
```

### Error Handling

- **401** — Missing or malformed Bearer token
- **403** — Invalid token
- **404** — Workspace or build not found
- **400** — Bad request (e.g. rename conflict)
- **500** — Internal error (check `message` field)

All error responses include a `detail` field:
```json
{"detail": "Workspace 'foo' not found"}
```
