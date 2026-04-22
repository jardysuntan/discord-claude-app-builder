## Session Summary

**1. RPS Tournament Game** — Built a full Rock-Paper-Scissors elimination tournament in `GamesScreen.kt` (`RpsTournamentFullScreen`, `RpsBracketView`, `RpsChampionsView`). Bracket generation, match recording, bye handling (groom gets auto-bye), 1st/2nd/3rd place crowning. Data model in `model/RpsTournament.kt`, persisted via `rps_tournaments` Supabase table.

**2. Crew Location Map** — Added live location sharing to `CrewScreen.kt` with `CrewLocationMap` composable. Platform location providers (`PlatformLocation.kt` expect/actual) for Android (LocationManager), iOS (CLLocationManager with retained references to prevent GC), WASM (browser Geolocation API). Leaflet map renders crew pins via `buildCrewMapHtml()` in `LeafletMapHtml.kt`. iOS required `NSLocationWhenInUseUsageDescription` in Info.plist.

**3. Golf Scorecard Enhancements** — Clear button (trash icon in top bar) with confirmation dialog. Per-hole score dropdowns now have a "Clear" option that DELETEs the row from Supabase. "Sit Out" toggle per player, persisted to `golf_rounds.sittingOut` jsonb column and synced across devices. Score reaction animations (Eagle/Birdie/Par/Bogey/Double/Triple+) with scale-bounce overlay in `ScoreReactionOverlay`. Changed "SI" label to "HI" (Hole Index).

**4. Real Data Migration** — Replaced all placeholder data (Shadow Creek, Cosmo hotel) with real bachelor party data: Revere Golf Club Concord/Lexington, Reflection Bay, two Henderson Airbnbs. Real scorecard data sourced from BlueGolf. Guest list: 14 confirmed + Bryan Cho pending. Dates: May 27 – Jun 1, 2026.

**5. Airbnb Housing Revamp** — Added `listingUrl`, `joinUrl`, `description`, `highlights`, `sqft`, `bedrooms`, `bathrooms` to housings schema. `amenities` changed from `List<String>` to `List<AmenityCategory>` (categorized). `HousingScreen.kt` fully rewritten with highlights checklist, categorized amenity chips, room assignments, action buttons (Maps/Listing/Co-Traveler).

**6. Critical Supabase Fix** — App was using `Content-Profile: app_yangzihesobachmobile` but PostgREST never exposed that schema (406 errors). Switched everything to `public` schema (`SCHEMA = "public"` in ConfigRepository, `schema = "public"` in SupabaseRealtime). Also fixed `get_app_config()` RPC ambiguity (PGRST203) by dropping duplicate functions. Fixed RPC calls to send `setBody("{}")` instead of `{"p_schema": "..."}`.

**7. Loading State** — Removed DemoData fallback entirely. App shows branded loading screen (`LoadingScreen` in App.kt) with shimmer placeholders until Supabase responds. Cache key bumped to `cached_config_v2` to invalidate stale data. Cache only used as offline fallback.

**8. UI Polish** — Replaced all `--` separators with `•` (bullet), `→` (arrow), or `—` (em dash). Added pulsing countdown animation on HomeScreen, staggered scale-bounce on day pills. "Booked by Albert/Daniel/Steven" labels added to Golf/Housing/Dining sections.

**User Preferences**: No emoji in UI (breaks WASM). Prefers clean, snappy animations. Wants real-time sync across all devices. Dislikes placeholder/demo data showing before real data loads.

**Current State**: App compiles for all 3 targets. WebSocket sync works. All data flows through `public` schema. The `public.get_app_config()` RPC is shared with another app (healthbrain) on the same Supabase project — it could get overwritten again.

---
_Previous session context:_
## Session Summary

**1. RPS Tournament** — Full Rock-Paper-Scissors elimination bracket in `GamesScreen.kt` (`RpsTournamentFullScreen`, `RpsBracketView`, `RpsChampionsView`). Groom auto-gets byes. Model: `model/RpsTournament.kt`. Supabase table: `rps_tournaments`.

**2. Crew Location Map** — Live location sharing in `CrewScreen.kt` (`CrewLocationMap`). Expect/actual `PlatformLocation.kt`: Android (LocationManager), iOS (CLLocationManager — **must retain at module level to prevent GC**), WASM (browser Geolocation via `@JsFun`). Added `NSLocationWhenInUseUsageDescription` to `iosApp/iosApp/Info.plist`. Map via `buildCrewMapHtml()` in `LeafletMapHtml.kt`.

**3. Golf Scorecard Enhancements** — Clear button (trash icon + confirm dialog). Per-hole "Clear" in dropdowns calls `deleteGolfScore()` (DELETE row, not null upsert — null was being restored by WebSocket refresh). "Sit Out" persisted to `golf_rounds.sittingOut` jsonb, syncs cross-device. `ScoreReactionOverlay`: EAGLE!(gold)/Birdie!(green)/Par(blue)/Bogey(orange)/Double Bogey(red)/Triple+(dark red) with scale-bounce animation. "SI" → "HI".

**4. Real Data Migration** — Replaced all placeholders. Venues: Revere Concord/Lexington, Reflection Bay (real BlueGolf scorecards), 2 Henderson Airbnbs, X-Pot, Din Tai Fung. 14 guests + Bryan Cho (pending). Dates: May 27–Jun 1, 2026. "Booked by" labels on Golf (Albert), Housing (Daniel), Dining (Steven).

**5. Airbnb Housing Revamp** — New columns: `listingUrl`, `joinUrl`, `description`, `highlights`, `sqft`, `bedrooms`, `bathrooms`. `amenities` changed from `List<String>` to `List<AmenityCategory>` (model in `Housing.kt`). `HousingScreen.kt` rewritten: highlights checklist, categorized amenity chips, room assignments, Maps/Listing/Co-Traveler buttons.

**6. CRITICAL: Supabase Schema Fix** — `Content-Profile: app_yangzihesobachmobile` caused 406s (schema not exposed by PostgREST). Switched to `SCHEMA = "public"` in `ConfigRepository.kt` and `SupabaseRealtime.kt`. Dropped duplicate `get_app_config()` overloads (PGRST203). Fixed RPC body from `{"p_schema":"..."}` to `"{}"`. **WARNING: Shares Supabase project with healthbrain app — `public.get_app_config()` was overwritten once by that app; could recur.**

**7. Loading State** — Removed `DemoData` fallback. `LoadingScreen` in `App.kt`: branded title + shimmer bars + spinner. Cache key bumped to `cached_config_v2` (invalidates stale Shadow Creek data). Cache = offline fallback only. Supabase is sole source of truth.

**8. UI Polish** — All `--` replaced: `•` (bullets), `→` (arrows), `—` (em dashes). Pulsing countdown on `HomeScreen`. Staggered scale-bounce on day pills. Styled date range with `buildAnnotatedString`. Removed debug println from `ConfigRepository.kt`/`SupabaseRealtime.kt`. Deleted `MapScreen.kt`.

**User Preferences**: NO emoji in UI (breaks WASM). Material Icons only. Clean snappy animations. Real-time cross-device sync. No stale/demo data ever shown. Terse UI text.

**Current State**: Builds all 3 targets. WebSocket sync confirmed working. All data in `public` schema. No known bugs. Potential risk: shared Supabase `get_app_config()` RPC conflict with healthbrain app.