# bridge-extract

Slim, public-internet-safe Fly.io service that exposes ONLY the two
endpoints the WereSoBach iOS app needs:

- `POST /api/v1/extract`            — LLM-powered structured extraction
- `POST /api/v1/extract-doc-text`   — server-side PDF/DOCX → text
- `POST /api/v1/geocode-venues`     — Mapbox forward geocoding
- `POST /api/v1/golf-course-lookup` — golfcourseapi.com lookup

The full discord-claude bridge (`api.py` on the Mac mini) keeps the
shell-out / Claude CLI / Path B / app-builder endpoints — those should
never be exposed publicly. This service shares the same `accounts.json`
schema (Bearer-token auth, encrypted account credentials) so existing
API keys keep working.

## First deploy

```bash
cd bridge-extract

# 1. Auth + create app (interactive — preserves the app name in fly.toml)
flyctl auth login
flyctl launch --no-deploy --copy-config --name wsb-bridge --region sjc

# 2. Persistent volume for accounts.json
flyctl volumes create bridge_data --region sjc --size 1

# 3. Secrets — CREDENTIAL_ENCRYPTION_KEY must match the parent bridge so
#    encrypted credentials decrypt on this side. MAPBOX_TOKEN and
#    GOLF_COURSE_API_KEY mirror the parent bridge's .env.
flyctl secrets set \
    CREDENTIAL_ENCRYPTION_KEY="$(cat ../.credential-key)" \
    MAPBOX_TOKEN="$(grep ^MAPBOX_TOKEN ../.env | cut -d= -f2-)" \
    GOLF_COURSE_API_KEY="$(grep ^GOLF_COURSE_API_KEY ../.env | cut -d= -f2-)"

# 4. Seed accounts.json onto the volume.
#    Easiest path: deploy once with empty volume, then SFTP the file.
flyctl deploy
flyctl ssh sftp shell
> put ../accounts.json /data/accounts.json
> exit
flyctl machine restart   # picks up the new accounts.json on boot

# 5. Smoke test
curl -sf https://wsb-bridge.fly.dev/healthz
# {"ok":true,"service":"bridge-extract"}
```

## Updating

```bash
cd bridge-extract
flyctl deploy
```

## Security notes

- This service does NOT mount any code from the parent bridge. Everything
  it needs (`accounts.py`, `llm_providers.py`, `config.py`) is vendored
  into this directory so it can be deployed without `app-builder-api`'s
  dependencies (workspaces, Claude CLI, GH runner config, etc.).
- `CREDENTIAL_ENCRYPTION_KEY` MUST equal the parent bridge's
  `.credential-key`, otherwise `Account.get_credential('llm')` returns
  `None` and every extract returns 400 ("No LLM credential set").
- All routes except `/healthz` require a Bearer token issued by the
  parent bridge's `/api/v1/register` endpoint. New accounts must be
  created on the parent bridge — this service is read-only for accounts.
