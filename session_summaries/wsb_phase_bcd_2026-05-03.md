# WereSoBach — Phase B/C/D session log (2026-05-03 morning)

## TL;DR
Shipped Phases B/C/D of the auto-fix pipeline. Found and fixed a real bug
in the import flow during the loop's first end-to-end run. Background
watcher is running on a 30-min cadence with a $75/day spend cap.

## What landed (commits on `main`, not pushed)

```
0d10ce9 fix(import): scope all emitted IDs by tripId so cross-trip slugs don't collide
ce05c3f test(ui): smoke covers Round-1 expand + cosmetic golf fix
bf7e856 chore(gitignore): exclude smoke + playwright test artifacts
       test(ui): iOS-sim end-to-end smoke harness via simctl + Tesseract OCR
4f4c851 feat(import): golf rounds + tees pipeline, gap-confirm flow, no-blanks UI
       test(loop): self-fix loop wrapper — smoke → claude-fixer → repeat
       test(loop): scheduling — foreground watcher + launchd installer
```

## What's running now

`wsb_loop_watch.sh` is launched in the background (PID 95660). Every 30
min:
1. Power gate (skip if on battery)
2. Sim gate (skip if no booted iPhone)
3. Run UI smoke (`--skip-import`, ~30s)
4. If clean → log + sleep
5. If dirty → invoke `claude -p` with a brief; capped at $4/iter, $75/day

State: `~/.wsb-loop-state.json`
Logs: `tests/ui-smoke/cron-runs/watch-YYYY-MM-DD.log`

To stop: `pkill -f wsb_loop_watch.sh`

## Bug found by the loop (this session)

The import committer was emitting global slug IDs (`reflection-bay`,
`yangzi-he`) — but the Supabase schema has these as global PKs. With
`Prefer: merge-duplicates`, the second trip's commit silently merged
into the first trip's row, so the new trip never got its own venue.
Round commits succeeded (FK satisfied), but the per-trip lookup the
Golf screen does returned nothing → "Course details pending" rendered
for Round 3.

Fixed by prefixing every emitted ID with an 8-char tripId chunk in
`ImportCommitter.commit` and rewriting all FK references in the same
payload. Mirror fix in the Python E2E + smoke seed helpers.

## Tooling discoveries (saved as helpers)

- iOS sim drops single-frame synthetic clicks. Use
  `cliclick dd:x,y w:80 du:x,y` for taps.
- iOS sim drops single-event drags. Use `dd → dm → du` for scrolls.
- Compose web is canvas-only. Playwright DOM queries are dead. OCR via
  Tesseract on simctl screenshots is the working alternative.
- Compose iOS routes inner Button taps to parent AppCard onClick. Tests
  can verify CTA visibility but cannot reliably auto-tap inner buttons.
- LaunchAgent jobs lack Accessibility grants → osascript -609 errors.
  Use a foreground watcher running under the user's terminal session.

## Smoke harness assertion coverage (17 hard checks)

```
Home:       Next Up header, Rounds/Crew pills, no forbidden tokens
Itinerary:  header, Venues Map, Revere mention, Reflection Bay mention
Golf:       header, Round 1, Revere card, Reflection Bay card,
            no forbidden tokens
Round 1:    Pairings, Group 1, Stroke play, Scorecard CTA visible,
            no null/TBD/undefined leaks
```

## What to test when back

The simulator now displays the freshly-imported trip with all the
correct data. Walk:
1. **Home** — "Next Up" card with first event, summary pills
2. **Itinerary** — map with 9 pins, day-by-day events
3. **Golf** — 3 rounds, all canonical course names. Tap Round 1 → see
   pairings (no empty parens for guests without nicknames anymore)
4. **Scorecard** — tap the button (manual, since synthetic taps hit the
   Compose iOS Card+Button click conflict). Verify the par/SI grid
   matches the API data (par 72, real stroke indices 1-18).

## What's left (pending if you want)

- Real-time sync test: open scorecard on two devices, tap a score,
  verify it appears on the other within 2s
- Scorecard tap probe via XCUITest (Swift in iosApp.xcodeproj) — would
  bypass the Compose iOS Card+Button conflict
- Slack/Discord notification on loop failure cycles
