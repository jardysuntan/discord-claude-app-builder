# App Builder HTTP API v1

HTTP API for building apps programmatically. Runs on port **8100** (configurable via `API_PORT`).

## Quick Start (New Consumers)

Three steps to go from zero to building apps:

```bash
# 1. Register — no auth required
curl -X POST https://your-server:8100/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"display_name": "My Bot", "email": "dev@example.com"}'

# Response: {"account_id": "acc_abc123", "api_key": "sk_live_xyz...", "message": "Save your API key..."}
# SAVE THE API KEY — it's shown once and cannot be retrieved.

# 2. Add your LLM key (required to build apps)
curl -X POST https://your-server:8100/api/v1/account/credentials/llm \
  -H "Authorization: Bearer sk_live_xyz..." \
  -H "Content-Type: application/json" \
  -d '{"data": {"api_key": "sk-your-openai-or-anthropic-key"}}'

# 3. Build an app
curl -X POST https://your-server:8100/api/v1/buildapp \
  -H "Authorization: Bearer sk_live_xyz..." \
  -H "Content-Type: application/json" \
  -d '{"description": "a todo list app with categories"}'
```

Check what you can do: `GET /api/v1/account` returns your capabilities and a setup checklist.

## Auth

All endpoints (except `/health` and `/register`) require a Bearer token:

```
Authorization: Bearer <your-api-key>
```

**Two types of tokens work:**
- **New API keys** (`sk_live_...`) — created via `/register` or `/account/keys`. Each account can have multiple keys.
- **Legacy token** (`.api-token` file) — maps to the admin account for backward compatibility.

## Account Management

### POST /api/v1/register

Register a new account. **No auth required.** Returns an API key (shown once).

```bash
curl -X POST http://localhost:8100/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"display_name": "My Service", "email": "dev@example.com"}'
```

**Response:**
```json
{
  "account_id": "acc_a1b2c3d4e5f6",
  "display_name": "My Service",
  "api_key": "sk_live_abc123...",
  "message": "Save your API key — it cannot be retrieved later."
}
```

### GET /api/v1/account

Get your account info, capabilities, and setup checklist.

```bash
curl http://localhost:8100/api/v1/account \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "account_id": "acc_a1b2c3d4e5f6",
  "display_name": "My Service",
  "role": "user",
  "capabilities": {
    "code_generation": {"enabled": false, "unlock": "POST /api/v1/account/credentials/llm"},
    "backend":         {"enabled": false, "unlock": "POST /api/v1/account/credentials/supabase"},
    "publish_ios":     {"enabled": false, "unlock": "POST /api/v1/account/credentials/apple", "alternative": "Request shared store access from admin"},
    "publish_android": {"enabled": false, "unlock": "POST /api/v1/account/credentials/google", "alternative": "Request shared store access from admin"}
  },
  "setup_checklist": [
    {"step": "Register account", "done": true, "hint": null},
    {"step": "Add LLM API key", "done": false, "hint": "POST /api/v1/account/credentials/llm with {\"api_key\": \"sk-...\"}"},
    {"step": "Add Supabase credentials (for backend)", "done": false, "hint": "POST /api/v1/account/credentials/supabase ..."},
    {"step": "Add Apple credentials (for iOS publishing)", "done": false, "hint": "..."},
    {"step": "Add Google credentials (for Android publishing)", "done": false, "hint": "..."}
  ]
}
```

**Capabilities** are dynamically computed from your credentials. Each tells you what's enabled and what to do to unlock it.

### Credentials (BYOK)

You bring your own keys. Credentials are encrypted at rest.

#### POST /api/v1/account/credentials/{type}

Set a credential. Types: `llm`, `supabase`, `apple`, `google`.

```bash
# LLM key (required for code generation)
curl -X POST http://localhost:8100/api/v1/account/credentials/llm \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": {"api_key": "sk-..."}}'

# Supabase (required for backend/database features)
curl -X POST http://localhost:8100/api/v1/account/credentials/supabase \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": {"project_ref": "your-project", "anon_key": "eyJ...", "management_key": "sbp_..."}}'

# Apple (required for iOS publishing, or request shared store access)
curl -X POST http://localhost:8100/api/v1/account/credentials/apple \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": {"team_id": "ABCDEF", "asc_key_id": "...", "asc_issuer_id": "...", "asc_key_path": "/path/to/key.p8"}}'

# Google (required for Android publishing, or request shared store access)
curl -X POST http://localhost:8100/api/v1/account/credentials/google \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": {"play_json_key_path": "/path/to/key.json", "keystore_path": "...", "key_alias": "...", "keystore_password": "...", "key_password": "..."}}'
```

**Response** always includes updated capabilities:
```json
{"credential_type": "llm", "status": "stored", "capabilities": {"code_generation": {"enabled": true}, ...}}
```

#### GET /api/v1/account/credentials

List which credential types are set (no secrets exposed).

```json
{"llm": true, "supabase": false, "apple": false, "google": false}
```

#### DELETE /api/v1/account/credentials/{type}

Remove a credential.

### API Keys

Each account can have multiple API keys (e.g. one per environment).

#### POST /api/v1/account/keys

Create a new API key.

```bash
curl -X POST http://localhost:8100/api/v1/account/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label": "production"}'
```

**Response:** `{"api_key": "sk_live_...", "label": "production", "message": "Save your API key..."}`

#### GET /api/v1/account/keys

List keys (prefix + label only, never the full key or hash).

```json
[
  {"prefix": "sk_live_", "label": "default", "created_at": "2026-03-29T..."},
  {"prefix": "sk_live_", "label": "production", "created_at": "2026-03-29T..."}
]
```

#### DELETE /api/v1/account/keys/{prefix}

Revoke a key by its prefix.

### Admin Endpoints

These require an admin-role account.

#### POST /api/v1/admin/whitelist

Grant shared store access (lets a user publish to your Apple/Google accounts).

```bash
curl -X POST http://localhost:8100/api/v1/admin/whitelist \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acc_target_user"}'
```

#### DELETE /api/v1/admin/whitelist/{account_id}

Revoke shared store access.

#### GET /api/v1/admin/whitelist

List accounts with shared store access.

## Workspace Scoping

Each account only sees its own workspaces. When you call `GET /workspaces`, `POST /buildapp`, etc., results are filtered to your account. Admin accounts see all workspaces.

Workspace responses now include `account_id` and `capabilities`:

```json
{
  "slug": "my-app",
  "path": "/projects/accounts/acc_abc123/myapp",
  "platform": "kmp",
  "owner_id": null,
  "account_id": "acc_abc123",
  "capabilities": {"code_generation": {"enabled": true}, ...}
}
```

Build responses include `warnings` for missing capabilities:

```json
{
  "build_id": "abc123",
  "status": "building",
  "warnings": ["Your app description mentions backend features but you haven't configured Supabase credentials."],
  "capabilities": {"code_generation": {"enabled": true}, "backend": {"enabled": false, "unlock": "..."}}
}
```

---

## Structured Extraction (LLM)

### POST /api/v1/extract

Extract structured JSON from a document using the caller's configured LLM key.
Multi-provider: Anthropic, OpenAI, Google (Gemini), Groq, DeepSeek, Mistral, OpenRouter.
Provider is auto-detected from the API key prefix (`sk-ant-*` → Anthropic, `gsk_*` → Groq, `AIza*` → Google, `sk-or-*` → OpenRouter, `sk-*`/`sk-proj-*` → OpenAI; DeepSeek/Mistral require explicit `provider`).

**Prerequisite:** set an LLM credential first via `POST /api/v1/account/credentials/llm`.

```bash
curl -X POST http://localhost:8100/api/v1/extract \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Trip to Paris June 10-15. Visiting Louvre and Eiffel Tower.",
    "json_schema": {
      "type": "object",
      "properties": {
        "trip": {"type": "object", "properties": {"destination": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}}},
        "venues": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}
      }
    }
  }'
```

**Body:**
- `text` (required) — the source document, as plain text. For PDF/markdown the caller must convert to text first.
- `json_schema` (required) — JSON Schema the model should fill
- `provider` (optional) — override auto-detection: `anthropic`, `openai`, `google`, `groq`, `deepseek`, `mistral`, `openrouter`
- `model` (optional) — override provider default (e.g. `"claude-opus-4-6"`, `"gpt-4o-mini"`, `"gemini-2.0-flash"`, `"llama-3.3-70b-versatile"`)
- `system_prompt` (optional) — override the default extractor prompt
- `temperature` (optional) — default `0.1`

**Success response (HTTP 200):**
```json
{"data": {"trip": {...}, "venues": [...]}, "provider": "anthropic", "model": "claude-haiku-4-5-20251001", "error": false}
```

**Error response (HTTP 200, `error: true`):**
```json
{"error": true, "error_message": "Anthropic 401: invalid x-api-key", "provider": "anthropic"}
```

Note: model errors return HTTP 200 with `error: true` (so callers don't auto-retry at the HTTP layer). Missing/invalid credentials return HTTP 400.

---

## Build & Create Endpoints

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
Phase values: `scaffolding`, `schema_design`, `schema_deploy`, `patching_credentials`, `building_android`, `building_web`, `building_ios`, `fixing`, `demo_android`, `demo_web`, `demo_ios`, `saving`, `deploying`, `complete`

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

### Analytics

#### GET /api/v1/analytics

Build analytics: success rates, average durations, per-workspace and per-operation breakdowns.

```bash
curl http://localhost:8100/api/v1/analytics \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "total_builds": 42,
  "successes": 35,
  "failures": 7,
  "success_rate": 83.3,
  "avg_duration_secs": 120,
  "total_duration_secs": 5040,
  "by_operation": {
    "buildapp": {"total": 15, "successes": 12, "failures": 3, "total_duration": 2400},
    "prompt": {"total": 20, "successes": 18, "failures": 2, "total_duration": 1800}
  },
  "by_workspace": {
    "todolist": {"total": 8, "successes": 7, "failures": 1}
  }
}
```

### Webhooks (Real-Time Build Events)

All async operations (`buildapp`, `prompt`, `demo`, `build`, `appraise`) accept an optional `webhook_url` field. For `buildapp`, **real-time events** are POSTed to that URL throughout the entire build lifecycle — not just at completion. Other operations send a single `complete` event when done.

#### Event Envelope

Every webhook POST sends this JSON body:

```json
{
  "build_id": "abc123",
  "timestamp": "2026-03-28T21:15:00Z",
  "event": "progress",
  "phase": "building_android",
  "message": "Claude writing code for Android target...",
  "elapsed_seconds": 45,
  "detail": {}
}
```

#### Event Types

| event | When | detail |
|-------|------|--------|
| `started` | Build request accepted | `{"app_name": "...", "description": "..."}` |
| `progress` | Phase change or meaningful update | `{}` |
| `issue` | Non-fatal problem (build continues) | `{"error": "..."}` |
| `platform_complete` | One platform finished building | `{"platform": "android\|web\|ios", "success": true}` |
| `demo_ready` | Demo URL available | `{"platform": "web", "url": "https://..."}` |
| `complete` | Build finished (success or fail) | `{"status": "success\|failed", "slug": "...", "platforms": {...}}` |
| `error` | Fatal unrecoverable error | `{"error": "...", "recoverable": false}` |

#### Phase Values

| phase | Description |
|-------|-------------|
| `scaffolding` | Creating project directory and template |
| `schema_design` | Claude designing database schema |
| `schema_deploy` | Creating Supabase tables |
| `patching_credentials` | Injecting Supabase credentials |
| `building_android` | Claude writing/fixing Android code |
| `building_web` | Building/fixing web (wasmJs) target |
| `building_ios` | Building/fixing iOS target |
| `fixing` | Auto-fix loop running |
| `demo_android` | Launching Android emulator demo |
| `demo_web` | Starting web server |
| `demo_ios` | Launching iOS simulator demo |
| `saving` | Git commit / checkpoint |
| `deploying` | Deploying to Cloudflare / hosting |
| `complete` | All done |

#### Example: Build with Webhooks

```bash
# 1. Start a build with a webhook endpoint
curl -X POST http://localhost:8100/api/v1/buildapp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "a todo list app",
    "app_name": "TodoList",
    "webhook_url": "https://example.com/webhook"
  }'
```

Your endpoint will receive a stream of POSTs:

```
→ {"event": "started",           "phase": "scaffolding",      "elapsed_seconds": 0}
→ {"event": "progress",          "phase": "scaffolding",      "elapsed_seconds": 3}
→ {"event": "progress",          "phase": "schema_deploy",    "elapsed_seconds": 18}
→ {"event": "progress",          "phase": "building_android", "elapsed_seconds": 25}
→ {"event": "issue",             "phase": "fixing",           "elapsed_seconds": 60}
→ {"event": "platform_complete", "phase": "building_android", "elapsed_seconds": 135}
→ {"event": "progress",          "phase": "building_web",     "elapsed_seconds": 140}
→ {"event": "platform_complete", "phase": "building_web",     "elapsed_seconds": 270}
→ {"event": "demo_ready",        "phase": "demo_web",         "elapsed_seconds": 280}
→ {"event": "complete",          "phase": "complete",         "elapsed_seconds": 290}
```

#### Example: Minimal Webhook Receiver (Python)

```python
from flask import Flask, request

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    event = request.json
    print(f"[{event['elapsed_seconds']}s] {event['event']}: {event['message']}")
    return "ok"

app.run(port=9999)
```

#### Design Notes

- **Fire-and-forget** — Webhook delivery never blocks or slows the build. If your endpoint is down, events are skipped (not retried).
- **5-second timeout** — Each webhook POST times out after 5s to prevent resource buildup.
- **Polling as fallback** — `GET /builds/{build_id}` is always available. Use webhooks for real-time UX, polling as backup.
- **No signing (yet)** — Events are not signed. Validate by checking `build_id` against builds you initiated.

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

## Integration Guide

### For New Consumers (Bots, Workflows, Services)

**Onboarding is 3 API calls — no config files, no env vars, no access to the server.**

```
register → set credentials → build apps
```

1. `POST /register` — get your `account_id` + `api_key`
2. `POST /account/credentials/llm` — provide your LLM API key (required)
3. `POST /account/credentials/supabase` — provide Supabase creds (optional, for backend)
4. `GET /account` — verify your capabilities and see what's unlocked
5. Start building: `POST /buildapp`

**That's it.** You don't need access to the server, `.env` files, or Discord. The API is self-service.

### Capability Tiers

| Credentials provided | What you can do |
|---------------------|----------------|
| None | Register, view account, manage keys |
| LLM key | Generate apps (code gen, builds, demos) |
| LLM + Supabase | Apps with database backends |
| LLM + Supabase + Apple | + publish to TestFlight |
| LLM + Supabase + Google | + publish to Play Store |
| Shared store access (admin-granted) | Publish using admin's store accounts |

### Full Workflow Example

```
register → set creds → planapp → buildapp → prompt (iterate) → build → demo → save
```

1. **Register**: `POST /register` → get API key
2. **Set LLM key**: `POST /account/credentials/llm` → enable code generation
3. **Plan** the app (optional): `POST /planapp` → get structured plan
4. **Build** from description: `POST /buildapp` → get `build_id`, poll until `status=success`
5. **Iterate** with prompts: `POST /workspaces/{slug}/prompt` → poll until done
6. **Build** for platform: `POST /workspaces/{slug}/build` → compile for web/android/ios
7. **Demo**: `POST /workspaces/{slug}/demo` → get demo URL
8. **Save**: `POST /workspaces/{slug}/save` → checkpoint progress
9. **Appraise** (optional): `POST /workspaces/{slug}/appraise` → quality check

### Python Example: End-to-End (New Consumer)

```python
import httpx
import asyncio

BASE = "https://your-server:8100/api/v1"


async def main():
    async with httpx.AsyncClient(timeout=300) as client:
        # 1. Register
        r = await client.post(f"{BASE}/register", json={
            "display_name": "My Workflow Bot",
        })
        api_key = r.json()["api_key"]
        headers = {"Authorization": f"Bearer {api_key}"}
        print(f"Registered: {r.json()['account_id']}")

        # 2. Set LLM key
        await client.post(f"{BASE}/account/credentials/llm", headers=headers, json={
            "data": {"api_key": "sk-your-key-here"},
        })

        # 3. Check capabilities
        r = await client.get(f"{BASE}/account", headers=headers)
        print(f"Code gen enabled: {r.json()['capabilities']['code_generation']['enabled']}")

        # 4. Build an app
        r = await client.post(f"{BASE}/buildapp", headers=headers, json={
            "description": "a workout tracker with exercises, sets, and reps",
            "app_name": "FitTrack",
        })
        build_id = r.json()["build_id"]

        # 5. Poll until complete
        while True:
            r = await client.get(f"{BASE}/builds/{build_id}", headers=headers)
            status = r.json()
            print(f"  [{status['elapsed_seconds']}s] {status['phase']}: {status['message'][:80]}")
            if status["status"] in ("success", "failed"):
                break
            await asyncio.sleep(5)

        slug = status["slug"]
        print(f"\nApp built: {slug}")

        # 6. List your workspaces (only yours — scoped by account)
        r = await client.get(f"{BASE}/workspaces", headers=headers)
        print(f"Your workspaces: {[w['slug'] for w in r.json()]}")


asyncio.run(main())
```

### Polling Pattern

All async operations (buildapp, prompt, demo, build, appraise) return a `build_id`. Poll `GET /builds/{build_id}` until `status` is `success` or `failed`:

```python
async def wait_for_build(client, build_id, headers, interval=5, timeout=600):
    import time
    start = time.time()
    while time.time() - start < timeout:
        r = await client.get(f"{BASE}/builds/{build_id}", headers=headers)
        data = r.json()
        if data["status"] in ("success", "failed"):
            return data
        await asyncio.sleep(interval)
    raise TimeoutError(f"Build {build_id} timed out")
```

### Error Handling

- **401** — Missing or malformed Bearer token
- **403** — Invalid token, or not authorized for this resource (e.g. accessing another account's workspace, or admin-only endpoint)
- **404** — Workspace or build not found
- **400** — Bad request (e.g. rename conflict, invalid credential type)
- **500** — Internal error (check `message` field)

All error responses include a `detail` field:
```json
{"detail": "Workspace 'foo' not found"}
```

### For Admin: Granting Shared Store Access

If you want a consumer to publish apps under your Apple/Google accounts without sharing credentials:

```bash
# Grant
curl -X POST http://localhost:8100/api/v1/admin/whitelist \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acc_their_account_id"}'

# Revoke
curl -X DELETE http://localhost:8100/api/v1/admin/whitelist/acc_their_account_id \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# List who has access
curl http://localhost:8100/api/v1/admin/whitelist \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### For Legacy Consumers

If you were using the old `.api-token` system, **nothing breaks**. Your existing token maps to the admin account automatically. All existing endpoints work identically — the only difference is that workspace responses now include `account_id` and `capabilities` fields.
