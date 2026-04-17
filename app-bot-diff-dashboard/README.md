# Path B dashboard

CI/CD-style visualization of the Path B pipeline:
`weresobach commit` → `Phase 2 sync` → `bottest push` → `Phase 3 audit` → `bridge PR`.

Hosted on Cloudflare Pages as a static site + one Pages Function that proxies GitHub's API.

## One-time setup

1. **Create a fine-grained PAT** at https://github.com/settings/personal-access-tokens/new
   - Resource owner: `jardysuntan`
   - Repositories: `weresobach`, `weresobachbottest`, `discord-claude-app-builder` (select all three)
   - Permissions (read-only):
     - Contents: Read
     - Actions: Read
     - Pull requests: Read
     - Metadata: Read
   - Copy the `github_pat_...` token.

2. **Login to Cloudflare** (opens browser):
   ```
   cd app-bot-diff-dashboard
   npx wrangler login
   ```

3. **Create the project + first deploy** (auto-provisions `app-bot-diff-dashboard.pages.dev`):
   ```
   npx wrangler pages project create app-bot-diff-dashboard --production-branch=main
   npx wrangler pages deploy public --project-name=app-bot-diff-dashboard --commit-dirty=true
   ```

4. **Set the secret** on both production and preview:
   ```
   npx wrangler pages secret put GH_TOKEN --project-name=app-bot-diff-dashboard
   # paste the github_pat_... value when prompted
   ```

5. Open `https://app-bot-diff-dashboard.pages.dev` — dashboard should show the last 25 weresobach commits with pipeline state.

## Redeploy (code changes)

```
npx wrangler pages deploy public --project-name=app-bot-diff-dashboard --commit-dirty=true
```

## Local dev

```
npx wrangler pages dev public
# then visit http://localhost:8788
```

For local dev to talk to GitHub, set `GH_TOKEN` in a `.dev.vars` file (gitignored) at this dir:
```
GH_TOKEN=github_pat_...
```

## Architecture

- `public/` — static site (HTML/CSS/JS). Fetches `/api/pipelines` and renders.
- `functions/api/pipelines.js` — Pages Function. Queries GitHub REST for:
  - last N commits on weresobach
  - last N Phase 2 workflow runs
  - last N commits on bottest (correlated to source SHA via commit message)
  - last N Phase 3 workflow runs
  - last N PRs on discord-claude-app-builder (matched by `gap-audit/<sha>` branch)
- `functions/api/audit/[runId].js` — Pages Function for gap-audit artifact details (future expansion).

All data fetched fresh per request. No database, no caching. ~5 GitHub API calls per dashboard load.
