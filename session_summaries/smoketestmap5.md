Here's the session summary:

---

**Session Summary — SmokeTestMap5 Build (2026-04-02)**

- **Built complete KMP app from skeleton.** The project started with only a placeholder `App.kt` showing "SmokeTestMap5" text, no `composeApp/build.gradle.kts`, no `gradle/` directory, and no `settings.gradle.kts` repositories. All infrastructure and source code were created from scratch.

- **Modeled after `smoketestmap4`.** Used the sibling project as a proven reference for build config, version catalog (`kotlin 2.1.0`, `compose-multiplatform 1.7.1`, `agp 8.7.2`), and the Leaflet.js map pattern. Package is `com.jaredtan.smoketestmap5`.

- **Created 14 files total.** Build system: `gradle/libs.versions.toml`, `gradle/wrapper/*`, `composeApp/build.gradle.kts`, updated `settings.gradle.kts` (added plugin/dependency repos), updated `build.gradle.kts` (added `kotlinSerialization` plugin), `local.properties`. Source: `App.kt`, `Venue.kt`, `platform/PlatformMapView.kt` (expect), `platform/PlatformWebView.kt` (expect), `platform/LeafletMapHtml.kt`, plus actual implementations for Android (2), iOS (2), WASM (2).

- **Architecture: expect/actual for maps.** `PlatformMapView` and `PlatformWebViewHtml` are expect declarations in commonMain. Android uses `AndroidView` + `WebView` with a height-fix script injection. iOS uses `UIKitView` + `WKWebView`. WASM uses `@JsFun` direct DOM manipulation with dynamic Leaflet CDN loading (no iframe — iframes don't work in Compose WASM).

- **5 SF coffee shops as data.** `Venue.kt` contains Blue Bottle, Sightglass, Ritual, Philz, and Equator with real coordinates, addresses, and ratings. `@Serializable` for WASM JSON encoding.

- **UI uses Material 3, no emoji.** `CenterAlignedTopAppBar` with `Icons.Default.LocationOn`, venue cards with `Icons.Default.Star` for ratings, `LazyColumn` layout, rounded clipped map (300dp). Follows the CLAUDE.md rule against Unicode emoji (broken on WASM).

- **Android compilation verified — BUILD SUCCESSFUL.** Ran `./gradlew composeApp:compileDebugKotlinAndroid` and it passed. iOS and WASM were not compiled (no iOS simulator or WASM browser test run).

- **No tests, no Supabase, no navigation.** This is a single-screen app. No unit tests written. No database integration. No multi-screen navigation.

- **Potential next steps:** Run on Android device/emulator to verify map rendering, test iOS build (`linkDebugFrameworkIosSimulatorArm64`), test WASM build (`wasmJsBrowserDistribution`), add interactive features (tap marker to scroll to card, search, filters).

- **`gradlew` and `gradle-wrapper.jar` were copied from `smoketestmap4`** since the skeleton didn't include them. The `local.properties` points to `~/Library/Android/sdk`.

---

Want me to save this to the project memory system for future sessions?