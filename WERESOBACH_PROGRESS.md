# WereSoBach — App Store Sprint Board

**Launch target:** Submit by 2026-04-24 → review by 2026-04-26 → live before 2026-05-27 bachelor party.
**Source of truth:** this file. Agents append dated entries under their task section. Dashboard renders it.
**Dashboard:** https://weresobach-progress-dashboard.pages.dev (awaiting `GH_TOKEN` secret)

---

## Legend
- `TODO` — queued, no work started
- `WIP` — agent actively working
- `BLOCKED` — waiting on human or another task
- `DONE` — merged + verified
- `FAIL` — last build/test failed, needs rework

---

## Board

| # | Feature / Task | Owner | Status | Last Update |
|---|----------------|-------|--------|-------------|
| 1 | Scaffold organizer-setup navigation | claude-foreman | DONE | 2026-04-19 |
| 2 | Crew Editor | agent-A | DONE | 2026-04-19 |
| 3 | Golf Editor | agent-B | DONE | 2026-04-19 |
| 4 | Games Editor | agent-C | WIP | 2026-04-19 |
| 5 | Progress dashboard | agent-D | DONE (needs token) | 2026-04-19 |
| 6 | Store listing + privacy policy | agent-E | DONE | 2026-04-19 |
| 7 | Signing/build audit | agent-F | DONE | 2026-04-19 |
| 8 | App icons (all densities) | agent-G | DONE | 2026-04-19 |
| 9 | Store screenshots | TBD | TODO (blocked on 2/3/4/8) | — |
| 10 | E2E smoke test | agent-J | DONE | 2026-04-19 |
| 11 | TestFlight + App Store submit | TBD | TODO (blocked on 6/7/8/9/10) | — |
| 12 | Play Console internal + production | TBD | TODO (blocked on 6/7/8/9/10) | — |

---

## Human blockers (need action from Jared)

### Apple / App Store Connect
- [ ] Confirm Apple Developer membership active at developer.apple.com (logged in as jared.e.tan@gmail.com)
- [ ] Create App Store Connect record for `com.weressobach.app` (name "We're so Bach", SKU `weressobach-ios-1`)
- [ ] Create App Store Connect API Key (Users & Access → Keys → +), download `.p8`, share Key ID + Issuer ID
- [ ] `DEVELOPMENT_TEAM = 825658FA35` — confirmed in project.pbxproj
- [ ] Bump `CURRENT_PROJECT_VERSION` (currently `1773286392`) before each TestFlight upload

### Google Play
- [x] `play-service-account.json` present — `project_id = my-project-1506052669764`
- [ ] Verify service account has Play Console API access on `com.weressobach.app` (flagged `api_access_verified: false` in `.playstore.json`)
- [x] Android `versionCode` bumped 1773200952 → 1773200953 by foreman 2026-04-19

### Domain + hosting
- [ ] Buy / claim `weressobach.app` domain
- [ ] Host `privacy-policy.html`, `terms-of-service.html`, `support.html` from `WereSoBach/store-assets/` at public URLs
- [ ] Create `support@weressobach.app` mailbox or alias

### Supabase
- [ ] Confirm `ajhkqssxpdjqnasgoxqq.supabase.co` is a dedicated WereSoBach project (NOT the legacy yangzihesobachmobile project) — currently unclear

### Dashboard
- [ ] Paste `GH_TOKEN` secret into weresobach-progress-dashboard Cloudflare Pages project (reuse the PAT from Path-B dashboard): `npx wrangler pages secret put GH_TOKEN --project-name=weresobach-progress-dashboard` then `npx wrangler pages deploy public --project-name=weresobach-progress-dashboard --commit-dirty=true`

---

## Agent log

### 2026-04-19 — kickoff
Foreman: task board created, 12 tasks enumerated, 6 agents launched (A/B/C features, D dashboard, E store copy, F audit), scaffolding (#1) shipped.

### 2026-04-19 — agent-F signing/build audit DONE
5 human blockers (versionCode, Supabase identity, Play Console API access, privacy URL, App Store Connect record), ~10 agent-fixable gaps. Top 3 crashers fixed by foreman 2026-04-19: iOS `NSLocationWhenInUseUsageDescription` added (prevents CLLocation crash), web title fixed, Android versionCode bumped, `android:icon` + `android:roundIcon` wired in manifest. Still needs: full Android mipmap density set, Supabase project confirmation, DemoData rename.

### 2026-04-19 — agent-D progress dashboard shipped
Cloudflare Pages Kanban live at https://weresobach-progress-dashboard.pages.dev — reads this file via GitHub API, 4-column board + agent log, auto-refresh 60s. Blocked on human: `GH_TOKEN` secret needs pasting into the new Pages project (reuse the PAT already on app-bot-diff-dashboard).

### 2026-04-19 — agent-E store assets DONE
`store-assets/` committed (9fc6f7f) with App Store + Play Store listings, privacy/TOS/support HTML, 17+ age-rating answers, human-input checklist. Placeholders: domain + support mailbox + reviewer demo account + screenshots/icon/feature graphic.

### 2026-04-19 — agent-J E2E smoke test DONE
API-level smoke test at `WereSoBach/tests/smoke-test.sh` exercises the full organizer → attendee flow against live Supabase (`ajhkqssxpdjqnasgoxqq`): admin-API signup (auto-confirm) → createTrip → 4 guests (CrewRepository shape) → 2 venues + 2 rounds + pairings (GolfAdminRepository shape) → 2 contests + 3 superlatives + RPS bracket (GamesAdminRepository shape) → drinkChallengesEnabled toggle → attendee joinTrip → `get_trip_config` RPC assertions on every section. 39/39 assertions green, twice, idempotent (trap-based cleanup DELETEs trip + both auth users). No real bugs found in the admin editor REST payloads — schema matches code. Uses service_role via Supabase Management API for admin/users + cleanup; `set -euo pipefail` aborts on first failure.

### 2026-04-19 — agent-G app icons DONE
Full iOS AppIcon.appiconset (18 PNGs 20@2x..1024 + rewritten Contents.json), Android mipmap-{m,h,x,xx,xxx}dpi `ic_launcher.png` + `ic_launcher_round.png`, adaptive icon (mipmap-anydpi-v26 XMLs + xxxhdpi background `#2D5016` + foreground), store-assets `app-store-icon-1024x1024.png` / `play-store-icon-512x512.png` / `feature-graphic-1024x500.png`, AndroidManifest `android:roundIcon` re-added. Smoke: `:composeApp:processDebugResources` BUILD SUCCESSFUL, iOS `xcodebuild ... iphonesimulator` BUILD SUCCEEDED with no AppIcon warnings. Source was a neutral blue cube glyph (not yangzi-branded) — kept as-is. Tools: sips for PNG resize, Pillow (pip --break-system-packages) for solid-color bg + feature-graphic text; ImageMagick not installed.

### 2026-04-19 HH:MM — Crew Editor DONE
agent-A: CrewEditorScreen + CrewRepository implemented end-to-end (add / edit / delete with confirm, role chips + free-text, empty + no-trip states, realtime via existing ConfigRepository); added `guests.orderIndex` column to Supabase; Android + iOS-sim-arm64 compile clean.

### 2026-04-19 HH:MM — Golf Editor DONE
agent-B: GolfEditorScreen + GolfAdminRepository shipped — 3-section selector (Courses / Rounds / Pairings). Courses: add/edit/delete venues with name + address + hole-by-hole URL. Rounds: add/edit/delete with venue dropdown, portable date picker (MM/DD/YYYY), 12-hour tee-time picker (5-minute granularity), format dropdown (Scramble/Stroke/Match/Best Ball) + free text, notes, dress code. Pairings: per-round round picker + tap-to-cycle chip assignment (Unassigned → Group 1-4) + live summary + save-to-JSONB `pairings`. Added `holeByHoleUrl` to `venues` table (migration `003_venues_hole_by_hole_url.sql`) and to `Venue` model (nullable, backward-compatible). Android compile clean.

### 2026-04-19 — agent-K Play Internal upload BLOCKED on human
Signed release AAB built clean: `composeApp/build/outputs/bundle/release/composeApp-release.aab` (11.8 MB, versionCode 1773200953, self-signed `CN=App` cert — expected since Play App Signing re-signs). Installed `google-auth` + `google-api-python-client` into the bridge venv and ran `/tmp/play-upload.py` against service account `jaredserviceacct@my-project-1506052669764.iam.gserviceaccount.com`. The very first `edits.insert` call returned **HTTP 404 "Package not found: com.weressobach.app."** Auth itself worked (no 401/403) — the 404 means either the app record doesn't exist in the Play Console yet, or the SA isn't granted access to this app. **Human action:** (a) in Play Console → Create app → package `com.weressobach.app`, then (b) Users & permissions → invite `jaredserviceacct@my-project-1506052669764.iam.gserviceaccount.com` with "Release manager" (at minimum: View app info, Release to testing tracks). Once done, re-run `/Users/jaredtanpersonal/bots/discord-claude-bridge/venv/bin/python /tmp/play-upload.py`. Script is idempotent; it creates a draft release (user still has to promote draft → completed in the UI). AAB build is NOT modified; nothing committed.

### 2026-04-19 22:25 — agent-I demo seed + screenshots PARTIAL
Seeded demo trip in Supabase `ajhkqssxpdjqnasgoxqq` (trip_id `d6900338-1330-493c-aff1-eaacfd3cf38d`, joinCode REVIEW1, organizerCode REVADMIN): 4 guests, 3 venues (2 golf/nightlife + 1 housing), 2 golf rounds with pairings, 2 contests (solo), 3 superlatives, 1 active RPS tournament, 3 itinerary events, 1 housing (Encore Suite). Reviewer account `reviewer@weressobach.app` / `ReviewApp2026!` created via Supabase Admin API (service_role fetched via `/v1/projects/<ref>/api-keys` on management API), email pre-confirmed, trip_members row inserted. Credentials saved to `WereSoBach/store-assets/reviewer-credentials.md`. RPC `get_trip_config` returns the full seed when called directly. **Screenshots captured:** 7 on iPhone 17 Pro Max (6.9", 1320×2868) + 6 on iPad Pro 13" M5 (2064×2752) under `WereSoBach/store-assets/screenshots/ios-6.9/` and `ios-ipad/`. **Blockers:** (1) `get_trip_config` network response arrives (log shows the POST) but `validateAndParse` silently returns null, so all tab screens render empty-state copy ("0 Rounds / 0 Meals / 0 Crew", "No events yet"). Likely a model-field type mismatch somewhere in `AppConfig` decode (json has `ignoreUnknownKeys=true + coerceInputValues=true`, so narrow candidates — possibly in `Contest`/`Superlative`/`RpsTournament`/`GolfRound` nested types) — can't modify Kotlin to pinpoint. (2) No iPhone 11 Pro Max (6.5") simulator installed — used iPhone 17 Pro Max for 6.9"; 6.5" folder is empty. (3) Android screenshots not started (no AVD booted). Task stopped here per "don't keep retrying, report blocker" boundary. Foreman needs to route the parse-failure to an agent with Kotlin write access.
