# WSB overnight e2e + bridge truncation fix — 2026-05-22

## Headline

- **20/20 e2e runs passed (100%), 41/41 assertions each.** Worker factory spawned 10 parallel workers twice; every run extracted Yangzi's bachelor-party doc into a fresh prod trip with all 3 golf rounds, both housings, 14 guests (Chase excluded), 15 events, plus the on-top-layered Olympics + Kudos counter, verified by readback from Supabase.
- **Root-caused the user's "golf rounds not saving" bug.** Was actually "golf rounds not being EXTRACTED" — Fly bridge silently truncated LLM output after `events` and dropped `housings` + `golfRounds` entirely. Fix deployed to `wsb-bridge.fly.dev`.
- **Patched the DateUtil NumberFormatException** that crashed the sim on Itinerary tap.
- **iOS build `1779454824`** built + installed to booted sim. Not yet uploaded to TestFlight.

## What broke

### 1. DateUtil.parse crashed Itinerary tab (real iPhone OK, sim crashed)

`DateUtil.parse(iso)` did `parts[0].toInt()` with zero guarding. Any event row with an empty or non-numeric date string (the user had one in their own trip) crashed Itinerary → `kotlin.NumberFormatException` → SIGABRT.

**Fix:** `parse()` now returns `Triple<Int,Int,Int>?`; six internal callers + `formatDayHeader`/`dayOfWeek*`/`isoToEpochMs` fall through to the raw string or a sensible default. Same defensive pattern `formatUpdatedTimestamp` already used.

Diff: `composeApp/src/commonMain/.../util/DateUtil.kt`

### 2. Bridge truncation dropped housings + golf rounds

`bridge-extract/llm_providers.py` had:
```python
_PROVIDER_TPM_BUDGET: dict[str, int] = {
    ...
    "gemini": 250000,
    ...
}
```
…but `GoogleProvider.name = "google"`. The lookup fell through to the `8000` default, then `out_reserve = min(8000, max(2000, 8000//3)) = 2666`. Gemini 2.5 Flash got a 2.6K-token output budget, which fit `trip_meta + guests + venues + events` (~6.9K chars = ~1.7K tokens of JSON) but stopped *just* before emitting `housings` and `golfRounds`. The JSON looked syntactically valid (`json-repair` patched the trailing braces), so the bridge returned a "success" response with the tail sections missing.

**This was the user's "golf rounds not saving" production bug.** Every non-trivial trip-doc import was silently dropping housings + golf rounds.

**Fix (deployed live to Fly):**
- Added `"google": 250000` key (kept `"gemini"` as legacy alias)
- Bumped `out_reserve` cap `8000 → 32000` tokens

Diff: `bridge-extract/llm_providers.py`. Fly image `wsb-bridge:deployment-01KS7W3R0BEDMQKAC1KMMFXPZY` redeployed via `flyctl deploy --remote-only --strategy immediate`.

### 3. iOS prompt under-counted multi-session golf days

After the bridge fix, single runs sometimes extracted 1 round instead of 3 when the doc had AM+PM golf at different courses on the same day. Added a tight "COUNT GOLF ROUNDS BEFORE EMITTING" reminder to the system prompt in both `BridgeClient.kt` (iOS) and `tests/e2e-llm-import/run_e2e.py` (harness). With the reminder + the bridge fix, 20/20 runs got the full 3-round split.

Diff: `composeApp/src/commonMain/.../data/BridgeClient.kt` (line 140-area)

## How verified

`tests/e2e-llm-import/run_e2e.py` (NEW, ~580 lines): single-run harness. Auth → fetch Google Doc via export URL → bridge `/api/v1/extract` with full schema + verbatim iOS prompt → assert parsed entity counts → commit to a fresh prod Supabase trip → add Kudos column + 3 Olympics contests via PATCH/POST → readback all entities → cleanup. Each run takes ~20s.

`tests/e2e-llm-import/worker_factory.py` (NEW): spawns N parallel workers via `concurrent.futures.ThreadPoolExecutor`, aggregates pass/fail, prints consistent-failure breakdown if any. Default `--workers 10 --require-pass-rate 0.9`.

Results (both rounds):
```
Round 1: 10/10 (100%)  41/41 asserts each   ~19-61s per worker (parallel)
Round 2: 10/10 (100%)  41/41 asserts each   ~16-45s per worker (parallel)
```

Aggregate JSON reports:
- `tests/e2e-llm-import/runs/factory-1779454735.json`
- `tests/e2e-llm-import/runs/factory-1779454789.json`

## What the e2e proves works (data-layer)

Per the user's brief:
- ✅ **Home/itinerary**: trip name, start/end dates, location ("Henderson, NV"), 15 events extracted + persisted
- ✅ **Golf tab**: all 3 rounds present in `golf_rounds` — Revere Concord (Thu AM), Revere Lexington (Thu PM), Reflection Bay (Fri AM); correct dates, venueIds, formats, dressCode
- ✅ **Games**: 3 Olympics contests + Kudos column added to `trips.pointsColumns`, `pointsCounterEnabled=true`
- ✅ **Housing**: both Airbnbs present in `housings` (golf Airbnb + party Airbnb), venueId FKs verified
- ✅ **Crew**: 14 guests committed, Chase He correctly excluded by the LLM via the "EXCLUDE guests the doc flags as not attending" prompt rule

What the e2e does NOT prove (out of scope, requires running the actual iOS app):
- UI rendering matches expectations (the data is correct in DB; whether each tab paints it right is a separate question)
- Tap interactions work (golf scorecard, etc.)
- Realtime sync between phones

## Build state

| Build | Purpose | TestFlight |
| --- | --- | --- |
| 1779068831 | Original May 17 submission | rejected 5/19 (4.3b + 2.3.6) |
| 1779389014 | 4.3 round-2 pivot (drinking games removed) | uploaded, never attached to submission |
| 1779416113 | Clear-X UX polish | uploaded, never attached to submission |
| **1779454824** | DateUtil fix + bridge prompt strengthen | **built + installed to sim, NOT yet uploaded** |

## Still to do tomorrow morning

1. **Upload `1779454824` to TestFlight via altool** (`store-assets/app-store-connect.md` has the recipe) so the user can attach it to the App Store version
2. **Resubmit App Store version 1.0** with build `1779454824` (or `1779416113` — either is fine, both have the pivot) + the rewritten metadata that's already in `store-assets/app-store-connect.md`
3. **Sanity-check iOS app on real device**: open the demo trip, tap Itinerary (should not crash now), import the user's actual planning doc, verify all 3 golf rounds + 2 housings show up
4. Optional: tighten the e2e to also drive iOS sim UI (cliclick / adb is OFF per `feedback_no_mouse_stealing_sim_tests.md` — would need a different approach like Xcode UI tests)

## Files touched (this session)

- `composeApp/src/commonMain/kotlin/com/weressobach/app/util/DateUtil.kt` — parse hardening
- `composeApp/src/commonMain/kotlin/com/weressobach/app/data/BridgeClient.kt` — golf-round count reminder
- `composeApp/build.gradle.kts` + `iosApp/iosApp.xcodeproj/project.pbxproj` — version bump → 1779454824
- `bridge-extract/llm_providers.py` (in `discord-claude-bridge` repo) — Google TPM budget key + out_reserve cap; deployed
- `tests/e2e-llm-import/run_e2e.py` (NEW)
- `tests/e2e-llm-import/worker_factory.py` (NEW)
- `tests/e2e-llm-import/runs/` — 22 run dirs + 2 factory aggregate reports

## Auth/credential notes (for future runs)

E2e harness signs in as the reviewer demo user (`jared.e.tan+wsbreview2026@gmail.com` / `wsb-password-123`). Each run creates a trip under that user's UUID `19a2d551-…b12`, then cleans up. The cleanup is best-effort; if `--keep` is passed (or if cleanup fails), test trips persist in prod and show up on the user's trip list — easy to delete manually if they accumulate. As of this session, 20 test trips were created + cleaned automatically.
