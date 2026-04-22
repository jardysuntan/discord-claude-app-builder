_Previous session context:_
## Session Summary

- **Build infrastructure bootstrapped from scratch**: `gradle/libs.versions.toml` (Kotlin 2.1.0, Compose MP 1.7.1, AGP 8.7.2, kotlinx-serialization 1.7.3), `composeApp/build.gradle.kts` (Android + iOS x64/arm64/simulator + wasmJs targets, `ComposeApp` static framework, `compose.material3` + `compose.components.resources` + serialization-json deps), Gradle wrapper 8.10, `local.properties` (SDK at `~/Library/Android/sdk`), `AndroidManifest.xml` (INTERNET permission, `MainActivity`), `res/values/styles.xml` (`Theme.SmokeTestMap`).

- **Venue data in commonMain**: `Venue.kt` defines a `@Serializable` data class and `sanFranciscoCoffeeShops` — 5 hardcoded SF shops (Blue Bottle, Sightglass, Ritual, Philz, Equator) with lat/lng/rating/address.

- **Map architecture uses expect/actual + Leaflet.js**: `platform/PlatformMapView.kt` (expect), `platform/PlatformWebView.kt` (expect), `platform/LeafletMapHtml.kt` (shared HTML generator — uses CDN `<link>`/`<script>` tags for Leaflet, NOT the base64-inlined approach described in CLAUDE.md).

- **Three platform actuals exist and compile**: Android (`AndroidView` wrapping `WebView`, injects JS height fix into body before `</body>` for `loadDataWithBaseURL`), iOS (`UIKitView` wrapping `WKWebView`, has deprecation warning for the older `UIKitView` API), wasmJs (`@JsFun` with direct DOM manipulation, CDN-loaded Leaflet, `ResizeObserver`, `crossOrigin: true` tile option, inserts a div before `#ComposeTarget` canvas).

- **App.kt is currently the original minimal scaffold** — centered "SmokeTestMap" text in `MaterialTheme`/`Surface`/`Column`. The full UI I wrote (`Scaffold` + `CenterAlignedTopAppBar` + `LazyColumn` with map + `VenueCard` using `Icons.Default.LocationOn`/`Star`) was reverted. **Do not revert that reset.** Platform files and venue data are ready but not wired in.

- **settings.gradle.kts was intentionally reset** to just `rootProject.name` + `include(":composeApp")` — no `pluginManagement`/`dependencyResolutionManagement` blocks. Builds still succeed (wrapper + version catalog handle resolution). **Do not add those blocks back.**

- **wasmJsMain/resources/index.html** exists with `<canvas id="ComposeTarget">` and `<script src="composeApp.js">`.

- **User preferences**: Minimal changes scoped tightly to the ask. No emoji in UI (WASM renders broken boxes — use Material Icons from `androidx.compose.material.icons.Icons`). Verify targets incrementally (Android first, then wasmJs separately). Keep responses concise.

- **Compilation verified**: `compileDebugKotlinAndroid`, `compileKotlinIosSimulatorArm64`, and `compileKotlinWasmJs` all pass. No runtime testing has been done on any platform.

- **Open threads / next steps**: (1) Wire the full UI into `App.kt` — import `PlatformMapView` and `sanFranciscoCoffeeShops`, build the Scaffold + map + venue list if the user requests it. (2) No runtime verification yet — installing on Android/iOS/browser is pending. (3) iOS `UIKitView` deprecation warning could be addressed by migrating to the newer API in Compose MP 1.7+. (4) CLAUDE.md specifies base64-inlined Leaflet for Android/iOS (CDN is blocked by WKWebView per the docs); current implementation uses CDN `<script src>` which may fail on iOS at runtime — likely needs switching to the `LeafletBundle.jsBase64` + `eval(atob(...))` pattern.

---
_Previous session context:_
## Session Summary

- **Build infrastructure bootstrapped from scratch**: `gradle/libs.versions.toml` (Kotlin 2.1.0, Compose MP 1.7.1, AGP 8.7.2, kotlinx-serialization 1.7.3), `composeApp/build.gradle.kts` (Android + iOS x64/arm64/simulator + wasmJs targets, `ComposeApp` static framework, `compose.material3` + `compose.components.resources` + serialization-json deps), Gradle wrapper 8.10, `local.properties` (SDK at `~/Library/Android/sdk`), `AndroidManifest.xml` (INTERNET permission, `MainActivity`), `res/values/styles.xml` (`Theme.SmokeTestMap`).

- **Venue data in commonMain**: `Venue.kt` defines a `@Serializable` data class and `sanFranciscoCoffeeShops` — 5 hardcoded SF shops (Blue Bottle, Sightglass, Ritual, Philz, Equator) with lat/lng/rating/address.

- **Map architecture uses expect/actual + Leaflet.js**: `platform/PlatformMapView.kt` (expect), `platform/PlatformWebView.kt` (expect), `platform/LeafletMapHtml.kt` (shared HTML generator — uses CDN `<link>`/`<script>` tags for Leaflet, NOT the base64-inlined approach described in CLAUDE.md).

- **Three platform actuals exist and compile**: Android (`AndroidView` wrapping `WebView`, injects JS height fix into body before `</body>` for `loadDataWithBaseURL`), iOS (`UIKitView` wrapping `WKWebView`, has deprecation warning for the older `UIKitView` API), wasmJs (`@JsFun` with direct DOM manipulation, CDN-loaded Leaflet, `ResizeObserver`, `crossOrigin: true` tile option, inserts a div before `#ComposeTarget` canvas).

- **App.kt is currently the original minimal scaffold** — centered "SmokeTestMap" text in `MaterialTheme`/`Surface`/`Column`. The full UI I wrote (`Scaffold` + `CenterAlignedTopAppBar` + `LazyColumn` with map + `VenueCard` using `Icons.Default.LocationOn`/`Star`) was reverted. **Do not revert that reset.** Platform files and venue data are ready but not wired in.

- **settings.gradle.kts was intentionally reset** to just `rootProject.name` + `include(":composeApp")` — no `pluginManagement`/`dependencyResolutionManagement` blocks. Builds still succeed (wrapper + version catalog handle resolution). **Do not add those blocks back.**

- **wasmJsMain/resources/index.html** exists with `<canvas id="ComposeTarget">` and `<script src="composeApp.js">`.

- **User preferences**: Minimal changes scoped tightly to the ask. No emoji in UI (WASM renders broken boxes — use Material Icons from `androidx.compose.material.icons.Icons`). Verify targets incrementally (Android first, then wasmJs separately). Keep responses concise.

- **Compilation verified**: `compileDebugKotlinAndroid`, `compileKotlinIosSimulatorArm64`, and `compileKotlinWasmJs` all pass. No runtime testing has been done on any platform.

- **Open threads / next steps**: (1) Wire the full UI into `App.kt` — import `PlatformMapView` and `sanFranciscoCoffeeShops`, build the Scaffold + map + venue list if the user requests it. (2) No runtime verification yet — installing on Android/iOS/browser is pending. (3) iOS `UIKitView` deprecation warning could be addressed by migrating to the newer API in Compose MP 1.7+. (4) CLAUDE.md specifies base64-inlined Leaflet for Android/iOS (CDN is blocked by WKWebView per the docs); current implementation uses CDN `<script src>` which may fail on iOS at runtime — likely needs switching to the `LeafletBundle.jsBase64` + `eval(atob(...))` pattern.