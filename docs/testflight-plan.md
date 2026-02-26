# TestFlight Automation — `/testflight` command

## Context

The bot can build iOS apps for simulator and deploy to physical devices via USB. This adds the final piece: build an app in Discord, upload it to TestFlight, and have anyone install it natively on their iPhone. This is the single-user MVP — the bot owner's Apple Developer account. Multi-user with web onboarding comes later.

## How it works

1. User runs `/testflight` in Discord (current workspace)
2. Bot sets Team ID + auto-increments build number in `Config.xcconfig`
3. Builds KMP release framework for arm64 (`linkReleaseFrameworkIosArm64`)
4. Archives via `xcodebuild archive` (release, iphoneos, automatic signing)
5. Exports IPA via `xcodebuild -exportArchive` with generated `ExportOptions.plist`
6. Uploads to App Store Connect via `xcrun altool --upload-app`
7. Reports success — build appears in TestFlight after Apple processing (~5-30 min)

## Setup checklist (do these first)

- [ ] Install Xcode from the Mac App Store
- [ ] Run: `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`
- [ ] Run: `sudo xcodebuild -license accept`
- [ ] Open Xcode, sign into your Apple Developer account (Settings > Accounts)
- [ ] Install iOS simulator runtime when prompted
- [ ] Apple Developer Program ($99/year) — https://developer.apple.com/programs/
- [ ] Create App Store Connect API key:
  - Go to https://appstoreconnect.apple.com/access/integrations/api
  - Click "Generate API Key"
  - Save the `.p8` file, Key ID, and Issuer ID
- [ ] Place `.p8` file at `~/.private_keys/AuthKey_<KEY_ID>.p8`
- [ ] Create app record in App Store Connect with matching bundle ID
- [ ] Set env vars in your `.env`:
  ```
  APPLE_TEAM_ID=<your team ID>
  ASC_KEY_ID=<your key ID>
  ASC_ISSUER_ID=<your issuer ID>
  ```

## Code changes to implement

### 1. `config.py` — add Apple credential env vars

```python
APPLE_TEAM_ID: str = os.getenv("APPLE_TEAM_ID", "")
ASC_KEY_ID: str = os.getenv("ASC_KEY_ID", "")
ASC_ISSUER_ID: str = os.getenv("ASC_ISSUER_ID", "")
ASC_KEY_PATH: str = os.getenv("ASC_KEY_PATH", "")  # optional, altool checks default paths
```

### 2. `platforms.py` — add iOS archive/export/upload methods

On `iOSPlatform` class (reuse existing `_run()` helper, `BuildResult`/`DeployResult` dataclasses):

- **`parse_bundle_id(ws_path)`** — read `PRODUCT_BUNDLE_IDENTIFIER` from `iosApp/Configuration/Config.xcconfig`
- **`set_team_id(ws_path, team_id)`** — write `TEAM_ID=<value>` in xcconfig
- **`set_build_number(ws_path, build_num)`** — write `CURRENT_PROJECT_VERSION=<value>` in xcconfig (use `int(time.time())` for uniqueness)
- **`archive(ws_path, team_id)`** — two-stage:
  1. `./gradlew composeApp:linkReleaseFrameworkIosArm64` (600s timeout)
  2. `xcodebuild archive -scheme iosApp -sdk iphoneos -configuration Release -archivePath build/iosApp.xcarchive CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=<team_id> -allowProvisioningUpdates`

Standalone functions (like existing `deploy_ios()`):

- **`export_ipa(archive_path, ws_path, team_id)`** — generates `ExportOptions.plist` (method=app-store, teamID, signingStyle=automatic), runs `xcodebuild -exportArchive`, returns IPA path
- **`testflight_upload(ipa_path, key_id, issuer_id)`** — runs `xcrun altool --upload-app`, returns `DeployResult` with helpful error messages

### 3. `commands/testflight.py` — new file, orchestration

`handle_testflight(ws_key, ws_path, on_status)`:
1. Validate credentials (APPLE_TEAM_ID, ASC_KEY_ID, ASC_ISSUER_ID)
2. Set team ID + build number in xcconfig
3. Archive → report progress
4. Export IPA → report progress
5. Upload → report success with bundle ID, build number, elapsed time

### 4. `parser.py` — add `/testflight` command

```python
case "/testflight":
    return Command(name="testflight")
```

### 5. `bot.py` — routing + help text

- Import `handle_testflight`
- Add `case "testflight"` (same pattern as `deploy`)
- Add `/testflight` to help text under "Build & Ship"

## Files to modify

| File | Change |
|------|--------|
| `config.py` | Add 4 Apple/ASC env vars |
| `platforms.py` | Add `parse_bundle_id`, `set_team_id`, `set_build_number`, `archive` on iOSPlatform; add `export_ipa`, `testflight_upload` standalone functions |
| `commands/testflight.py` | **New file** — `handle_testflight()` orchestrator |
| `parser.py` | Add `/testflight` case |
| `bot.py` | Import + route `testflight` command, update help text |

## Verification

1. `/testflight` → should archive, export IPA, upload, report success
2. Check App Store Connect — build should appear after processing
3. Missing credentials → clear error message listing what's needed
4. Missing app record → clear error pointing to App Store Connect
