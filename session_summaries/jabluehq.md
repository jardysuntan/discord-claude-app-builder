## Session Summary

- **Built JablueHQ from scaffold**: Complete KMP Compose Multiplatform app with 5 tabs (Overview, Kanban, Campaigns, Timeline, Work Log). All shared UI in `composeApp/src/commonMain/`. Gradle build system set up with Kotlin 2.1.0, Compose 1.7.3, Ktor 3.0.3. Targets: Android, iOS, wasmJs. Builds verified for all.

- **Supabase backend with direct REST queries**: `ConfigRepository.kt` fetches from 4 separate table endpoints (`/rest/v1/campaigns`, `/todos`, `/completed`, `/app_meta`) instead of the shared `get_app_config()` RPC — the RPC was overwritten by another app on the same Supabase instance. The RPC was restored but direct queries are more resilient. Schema expanded with `skills`, `daily_logs`, `daily_log_entries`, `daily_log_entry_skills`, `skill_xp_events` tables (added externally to `schema.sql`).

- **Kanban drag-and-drop is click-to-drag**: Uses `detectDragGestures` (not long-press). Cards inside a regular `Column` with `verticalScroll` (not `LazyColumn`) to avoid gesture conflicts. Floating overlay card follows cursor with rotation + shadow. Ghost placeholder shows in original position. Vertical reordering within columns updates `sortOrder` in Supabase. Cross-column drag changes priority. Cursor changes to grab/grabbing on wasmJs via `expect/actual` pattern in `platform/CursorUtils.kt`.

- **Expandable card descriptions**: Kanban cards tap to expand showing step-by-step instructions from `todos.description`. Animated chevron + `AnimatedVisibility`. All 10 seed todos have real actionable descriptions in Supabase and `seed.sql`.

- **Work Log tab (5th tab)**: `WorkLogScreen.kt` shows completed items grouped by category (shipped/spec/decision/infra/research) with filter chips. 40 real entries seeded. `completed` table has `category` and `model` columns.

- **Auto-refresh polling**: `DashboardViewModel.kt` polls Supabase every 10s via coroutine. Top bar shows "Last synced: Xs ago" with spinning sync icon during refresh. Manual refresh button still works.

- **User preferences**: Dark theme (#0F0F1A bg, #1E1E2E cards, #3B82F6 primary). No emoji in UI (WASM renders them as broken boxes) — use Material Icons mapped via `ui/IconMapper.kt`. Campaign icons stored as text strings ("rocket", "runner", etc.) in DB, mapped to `Icons.Filled.*`. Full viewport layout, no max-width container.

- **Current state**: Everything compiles and builds for wasmJs (`wasmJsBrowserDistribution`) and Android. The app is functional end-to-end with live Supabase data. The `get_app_config()` RPC was just restored but `ConfigRepository` uses direct table queries as primary approach.

- **Key files**: `App.kt` (tab routing), `KanbanScreen.kt` (most complex — drag system), `DashboardViewModel.kt` (state + polling), `ConfigRepository.kt` (Supabase REST), `Models.kt` (data classes), `Theme.kt`, `IconMapper.kt`, `WorkLogScreen.kt`, `OverviewScreen.kt`, `CampaignsScreen.kt`, `TimelineScreen.kt`.

- **Open threads**: The schema now includes RPG skills + daily log tables (added externally) but no corresponding Kotlin UI or models yet. The `ViewModel` methods `moveTodoToColumn` and `reorderWithinColumn` persist sort orders to Supabase but haven't been tested end-to-end in a browser.

---
_Previous session context:_
## Session Summary for Future AI

- **Built complete JablueHQ KMP app from scaffold**: 5-tab life dashboard (Overview, Kanban, Campaigns, Timeline, Work Log) in `composeApp/src/commonMain/`. Build system: Kotlin 2.1.0, Compose 1.7.3, Ktor 3.0.3. Targets Android, iOS, wasmJs — all compile and `wasmJsBrowserDistribution` succeeds.

- **Supabase REST architecture**: `ConfigRepository.kt` uses **direct table queries** (not the shared `get_app_config()` RPC) because the RPC was overwritten by another app on the same Supabase instance. Fetches `/rest/v1/campaigns`, `/todos`, `/completed`, `/app_meta` separately. The RPC was restored but direct queries are the primary path. Supabase URL and anon key are hardcoded in `ConfigRepository.kt`.

- **Kanban drag-and-drop is click-to-drag**: Uses `detectDragGestures` (immediate, not long-press). Cards in a `Column` + `verticalScroll` (not `LazyColumn`) to avoid gesture conflicts. Floating overlay card follows cursor with `graphicsLayer { rotationZ=2.5f, shadowElevation=24f }`. Ghost placeholder at 25% opacity. Cross-column drag changes priority; vertical drag changes `sortOrder`. Cursor grab/grabbing on wasmJs via `expect/actual` in `platform/CursorUtils.kt` using `kotlinx.browser.document`.

- **Expandable card descriptions**: Kanban cards have a chevron that toggles `AnimatedVisibility` to show `todos.description`. All 10 seed todos have step-by-step instructions. Null descriptions handled — no chevron shown.

- **Work Log tab**: `WorkLogScreen.kt` groups `completed` items by `category` (shipped/spec/decision/infra/research) with filter chips. 40 entries seeded. `completed` table has `category text` and `model text` columns.

- **Auto-refresh polling**: `DashboardViewModel.kt` polls every 10s via coroutine. Top bar shows sync status ("Xs ago") with spinning icon. `lastSyncedAt`/`isSyncing` tracked in `DashboardState`.

- **UI rules**: Dark theme (`#0F0F1A` bg, `#1E1E2E` cards, `#3B82F6` primary blue) in `ui/Theme.kt`. **No emoji in UI** — WASM renders them as broken boxes. Campaign icons are text strings ("rocket", "runner") mapped to Material Icons in `ui/IconMapper.kt`. Full viewport layout, no max-width.

- **Schema expanded externally**: `schema.sql` now includes `skills`, `daily_logs`, `daily_log_entries`, `daily_log_entry_skills`, `skill_xp_events` tables with RLS and a `set_updated_at()` trigger. The `get_app_config()` RPC was updated to include these tables. **No corresponding Kotlin models, repository methods, or UI screens exist yet** — this is the main open thread.

- **Key files**: `App.kt` (5-tab routing), `KanbanScreen.kt` (most complex — full drag system), `DashboardViewModel.kt` (state + polling + reorder), `ConfigRepository.kt` (Supabase REST + CRUD), `Models.kt` (data classes), `WorkLogScreen.kt`, `OverviewScreen.kt`, `CampaignsScreen.kt`, `TimelineScreen.kt`, `platform/CursorUtils.kt` (expect/actual).

- **What's next**: Build UI for the RPG skills/daily log system using the new schema tables. The `ViewModel` methods `moveTodoToColumn`/`reorderWithinColumn` persist to Supabase but haven't been browser-tested end-to-end.