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