# WereSoBach progress dashboard

Kanban view of the WereSoBach App Store sprint. Reads
`WERESOBACH_PROGRESS.md` from `jardysuntan/discord-claude-app-builder@main`
on every request and renders the Board table + Agent log. Auto-refreshes
every 60s.

## Deploy

```
cd weresobach-progress-dashboard
npx wrangler pages project create weresobach-progress-dashboard --production-branch=main
npx wrangler pages secret put GH_TOKEN --project-name=weresobach-progress-dashboard   # paste read-only PAT on discord-claude-app-builder
npx wrangler pages deploy public --project-name=weresobach-progress-dashboard --commit-dirty=true
```

You can reuse the PAT already set on `app-bot-diff-dashboard` — it already
has `Contents: Read` on `discord-claude-app-builder`. One-liner to copy it:

```
# Print the existing GH_TOKEN from app-bot-diff-dashboard, then pipe into this project
npx wrangler pages secret list --project-name=app-bot-diff-dashboard   # confirm GH_TOKEN is set
# There is no wrangler "secret get" — re-paste the value you used originally:
npx wrangler pages secret put GH_TOKEN --project-name=weresobach-progress-dashboard
```

## Local dev

```
echo 'GH_TOKEN=github_pat_...' > .dev.vars
npx wrangler pages dev public
# open http://localhost:8788
```

## Editing the board

The dashboard is a pure view over `WERESOBACH_PROGRESS.md`. To move a card,
edit the Board table in that file (commit to main). The dashboard picks it
up on the next 60s refresh (or Refresh button).
