# SwiftUI + KMP iOS Integration — Research & Planning

## TL;DR

Switch generated iOS apps from Compose Multiplatform to SwiftUI consuming a shared KMP module that contains business logic, networking, models, and (shared) ViewModels exposing `StateFlow`. Use **SKIE** (Touchlab) as the Kotlin↔Swift bridge so `Flow`, suspend functions, sealed classes, and enums cross the boundary cleanly. Keep Compose for Android, keep CMP for Web. This is the path leading KMP teams (Touchlab, John O'Reilly's PeopleInSpace/Confetti, Axel Springer, many late-2024/2025 production posts) converge on when iOS UX quality matters.

## Why this matters

CMP on iOS reached stable with 1.8.0 in May 2025 and scrolling physics/text editing have improved dramatically, but a long tail of native-feel gaps persists that iPhone users *notice*:

- **Keyboard avoidance inside Scaffold** still scrolls incorrectly in common layouts (open GitHub issues #4902, #3621).
- **Gesture conflicts** between Compose scroll/drag and native iOS gestures inside `UIKitView` (#5026, #4818).
- **No first-class SF Symbols, Dynamic Type, VoiceOver rotors, contextual menus, `.refreshable`, `.searchable`, swipe-to-delete, share sheets, Live Activities, widgets, iOS 18 control center entries, haptic feedback patterns, sheet detents, `.interactive` dismiss, App Intents, focus system, keyboard shortcuts.**
- Default widgets look Material-ish; reaching iOS parity requires a parallel `iosMain` theme effectively reimplementing SwiftUI.
- Compose renders through Metal to its own canvas, so accessibility is bridged, not native — screen reader experience is second-class.

For a consumer app targeting iPhone users as the primary audience, these gaps are not cosmetic; they are the difference between "feels like a real iOS app" and "feels like a port." The 60–80% code-reuse ceiling from a native-UI KMP architecture is usually a better trade than 90%+ reuse with an uncanny-valley UI.

Sources: JetBrains blog on CMP 1.8.0 stable, KMPship 2025 stable post, jacobras.medium.com "Getting the native iOS look & feel in your Compose Multiplatform app", JetBrains compose-multiplatform issues #3621/#4818/#4902/#5026.

## Recommended architecture

```
myapp/
├── shared/                         # KMP module — the contract
│   ├── src/commonMain/kotlin/
│   │   ├── domain/                 # pure Kotlin: models, use-cases
│   │   ├── data/                   # Ktor, SQLDelight, repos
│   │   ├── presentation/           # ViewModels exposing StateFlow
│   │   └── di/                     # Koin modules
│   ├── src/androidMain/kotlin/     # Android-only adapters
│   └── src/iosMain/kotlin/         # iosApp-facing helpers (KoinIOS, factories)
│
├── androidApp/                     # Jetpack Compose UI (unchanged from current)
│
├── iosApp/                         # NEW: pure SwiftUI, consumes `shared.framework`
│   ├── iosApp.xcodeproj/
│   ├── iosApp/
│   │   ├── iOSApp.swift            # @main, calls KoinIOSKt.doInitKoinIos()
│   │   ├── Screens/                # SwiftUI views
│   │   ├── ViewModels/             # Thin @Observable wrappers around shared VMs
│   │   ├── DI/                     # Koin resolver helpers
│   │   └── DesignSystem/           # iOS-native components (SF Symbols, theme)
│   └── Package.swift               # (if going SPM route)
│
└── webApp/                         # CMP Web (unchanged)
```

**Boundary rule:** `shared/` exposes `StateFlow<UiState>`, suspend intents (`onSubmit()`, `onRefresh()`), and plain data classes. `iosApp/` owns navigation, theming, layout, keyboard/gesture handling, and any iOS-specific side effects (haptics, `ShareLink`, `openURL`).

## Integration patterns

### Kotlin framework → Swift

Two options, both viable in 2025:

1. **`cocoapods { }` Gradle block** (legacy but still dominant): Gradle builds `shared.framework` and generates a `.podspec`. Xcode consumes it via CocoaPods. Xcode run-script phase (`embedAndSignAppleFrameworkForXcode`) is auto-wired. This is what 90% of tutorials and existing KMP apps use today.
2. **Direct SPM export** (Kotlin 2.0+): `kotlin { iosX64 { binaries.framework { baseName = "shared" } } }` + the `XCFramework` Gradle task + a `Package.swift` that references the built XCFramework. Either hand-written or via `multiplatform-swiftpackage` / **KMMBridge** (Touchlab). KMMBridge automates publishing XCFramework ZIPs and writing `Package.swift`, which is ideal if the iOS app lives in a separate repo from the Kotlin code — overkill for a single-repo generated app.
3. **Swift Export (experimental, Kotlin 2.2.20+)**: Bypasses the ObjC intermediary. Cleaner Swift signatures, but still experimental in early 2026 — not safe for code-gen output yet.

**Recommendation for the bot:** Use the **Gradle `cocoapods` block** for generated apps. It is the most documented path, has the fewest moving parts for a single-repo KMP project, and matches the default KMP wizard output. Revisit SPM once Swift Export stabilizes (likely 2026 H2).

### State: Flow → @Observable / Combine

SwiftUI has no native `StateFlow` equivalent. The shared module exposes `StateFlow<UiState>`; the iOS side must collect it and republish into an `ObservableObject`/`@Observable`.

Three approaches, ranked:

1. **SKIE + `@Observable` wrapper (recommended)**. SKIE converts `Flow<T>` to Swift `AsyncSequence` with preserved generics and handles bidirectional coroutine cancellation automatically. You write:
   ```swift
   @Observable final class HomeViewModel {
       private let shared: SharedHomeViewModel  // Kotlin
       var state: HomeUiState = .loading
       private var task: Task<Void, Never>?

       init(shared: SharedHomeViewModel) {
           self.shared = shared
           task = Task { [weak self] in
               for await s in shared.state {    // SKIE-generated AsyncSequence
                   self?.state = s
               }
           }
       }
       deinit { task?.cancel(); shared.clear() }
   }
   ```
   Zero annotations in Kotlin. Works with `@Observable` (iOS 17+) and also plain `ObservableObject` + `@Published` on older targets.

2. **KMP-NativeCoroutines (Rick Clephas)**. Requires annotating every exposed Flow/suspend with `@NativeCoroutines` / `@NativeCoroutinesState` in Kotlin. More explicit, supports Combine `Publisher` and RxSwift out of the box, and has companion library **KMP-ObservableViewModel** that makes a shared `ViewModel` subclass that *already* looks like an `ObservableObject` to Swift. More mature than SKIE for Combine-heavy codebases.

3. **Manual callbacks**. Expose a subscription helper in `iosMain` that takes an `(T) -> Unit` closure and returns a `Cancellable`. Works without any compiler plugin but is verbose and leaks easily.

**Recommendation:** SKIE. It is now the default in new Touchlab starter templates, requires no per-API Kotlin annotations (critical for code-gen quality — less for Claude to forget), and handles sealed classes/enums with exhaustive Swift `switch`.

### ViewModels: shared vs iOS-specific

Two architectural camps:

- **Camp A — Shared ViewModels in `commonMain`**: Extend `androidx.lifecycle.ViewModel` (now a KMP artifact) from `commonMain`, expose `StateFlow<UiState>`, expose intent functions. Swift wraps in a thin `@Observable`. Pros: one source of truth for presentation logic, cheapest parity. Cons: SwiftUI's idioms (binding, `@FocusState`, view-scoped state) live in Swift anyway, so the wrapper can get fat; iOS-specific UI state (keyboard focus, sheet presentation) leaks into either layer awkwardly.
- **Camp B — Shared use-cases, platform ViewModels**: `shared/` exposes repositories and use-cases (pure suspend functions / Flows). Each platform writes its own ViewModel. Pros: cleanest idiomatic SwiftUI (`@Observable` + `@State` + `@FocusState` without wrapper indirection), Android gets idiomatic Jetpack Compose state. Cons: logic duplication risk, drift.

**Leaders' consensus (2024–2025):** Camp A with a thin adapter. Touchlab's samples, PeopleInSpace, Confetti, D-KMP-sample, KMP-ObservableViewModel all use shared VMs. The adapter pattern is small (<30 LOC per screen) and the parity payoff is large.

**Recommendation for the bot:** Shared ViewModels in `commonMain` exposing `StateFlow<ScreenUiState>` sealed hierarchies + suspend intent functions. iOS wraps via a code-gen-template `@Observable` adapter per screen.

### Navigation

SwiftUI `NavigationStack` offers swipe-back, interactive dismiss, deep-link restoration that iPhone users expect. A KMP navigation library (Decompose, Voyager, Compose Navigation) typically does not.

Options:
1. **Native `NavigationStack` on iOS, Compose Navigation on Android, route model duplicated**. Simple, idiomatic, drift risk.
2. **Decompose for navigation state in `shared/`, native renderers on each platform**. Decompose is UI-agnostic — its "components" own navigation state, child lifecycles, and back-handling. Swift side reads the current active child from a StateFlow and renders the matching SwiftUI view. This is the pattern Arkady Ivanov documents in Decompose samples and the approach the D-KMP-sample project champions.
3. **Voyager**. Ruled out — it is Compose-first and forces Compose screens on iOS.

**Recommendation:** **Start with option 1** (native `NavigationStack`, small duplicated `enum Route` on each side, typically 10–30 lines per platform). Option 2 (Decompose) is worth considering only for deeply nested flows with complex back-stack requirements. For a code-gen tool producing mostly 3–8 screen apps, the duplication is trivial and the iOS UX payoff is large.

### Dependency injection

Koin remains the dominant KMP DI choice. The classic interop issue: `get<T>()`/reified generics don't exist in Swift.

**Pattern (`KoinIOS.kt` in `iosMain`):**
```kotlin
// commonMain
fun initKoin(appModule: Module) = startKoin { modules(appModule, platformModule) }

// iosMain
fun doInitKoinIos() { initKoin(...) }  // called from Swift

class KoinHelper : KoinComponent {
    fun homeViewModel(): HomeViewModel = get()
    fun settingsViewModel(): SettingsViewModel = get()
    // one factory method per VM the iOS app needs
}
```
Swift calls `KoinIOSKt.doInitKoinIos()` in `iOSApp.init`, then instantiates `KoinHelper()` and asks it for typed VMs. No reified resolution from Swift.

Alternative: per-VM top-level factory functions exported from `iosMain`. Slightly less magic than a helper class, equally type-safe. This is what PeopleInSpace and Confetti do.

**Recommendation:** Per-VM top-level factory functions in `iosMain` (e.g. `fun makeHomeViewModel(): HomeViewModel = KoinPlatform.getKoin().get()`). Easier for code-gen to produce one function per screen, and Swift call sites are self-documenting.

## Progressive adoption path

Even though the bot generates fresh apps, the template can support a **mix-and-match mode** for users who want CMP for a few screens (charts, canvas, complex animations that would be painful to rewrite) and SwiftUI for the rest:

- **CMP inside SwiftUI:** `ComposeUIViewController { YourScreen() }` on the Kotlin side, then in Swift wrap it in a `UIViewControllerRepresentable` and embed in any SwiftUI `NavigationStack`.
- **SwiftUI inside CMP (rare):** `UIKitViewController({ UIHostingController(rootView: YourSwiftUIView()) })` from Compose. Useful for CMP apps that need a sprinkle of native (Stripe, MapKit, PhotoKit, Live Text).

**Recommendation:** Default is **100% SwiftUI iOS**. Offer an opt-in `/buildapp --ios-hybrid` flag later that allows specific screens to fall back to CMP.

## Tooling

### SKIE
- Gradle plugin: `co.touchlab.skie` in the `shared` module.
- Zero-config out of the box. Generates `SkieSwiftFlow`/`SkieSwiftStateFlow` types in the Swift framework alongside `AsyncSequence` conformance.
- Additional wins: Kotlin enums → proper Swift `enum` (exhaustive `switch`), sealed classes → Swift enums with associated values, default-arguments preserved, suspend functions → `async throws` with cancellation.
- License: Apache 2.0. No paid tier needed for OSS / generated apps.

### KMP-NativeCoroutines
- Alternative to SKIE. Use if Combine interop is a hard requirement (`@NativeCoroutinesCombine`). More mature, more verbose.
- Don't mix with SKIE.

### Build setup — recommendation for the bot
- Single-repo structure: `shared/` with `cocoapods { }` block + `iosApp/Podfile` referencing `../shared` local pod.
- Xcode project: one target (`iosApp`), Swift Package dependencies only (no third-party Pods besides the shared framework). Keep `Podfile` minimal.
- Gradle tasks to expose: `:shared:podInstall`, `:shared:embedAndSignAppleFrameworkForXcode`.
- CI/local runs: `xcodebuild` against `iosApp.xcworkspace`.

### Xcode project structure (generated)
```
iosApp/
├── iosApp.xcworkspace
├── iosApp.xcodeproj
├── Podfile
├── Podfile.lock
└── iosApp/
    ├── iOSApp.swift            # @main
    ├── Assets.xcassets
    ├── Info.plist
    ├── Screens/
    │   ├── HomeView.swift
    │   └── HomeViewModel.swift  # @Observable wrapper
    ├── DesignSystem/
    │   ├── Theme.swift
    │   └── Components.swift
    └── Navigation/
        └── AppRoute.swift
```

## Real-world examples

1. **PeopleInSpace** — `github.com/joreilly/PeopleInSpace` — John O'Reilly's canonical KMP sample, featured in JetBrains docs. SwiftUI iOS + Compose Android + Wear + Desktop + Web. Clean minimal Koin-on-iOS via `KoinHelper`, shared VM via `KMM-ViewModel` library, manual Flow collection. Great reference for the bot's template.

2. **Confetti** — `github.com/joreilly/Confetti` — larger conference app by same author. GraphQL backend (Apollo), SwiftUI iOS, Compose Android/Wear. Shows navigation handled natively per platform, widget/watchOS targets consuming shared module. Realistic production-ish architecture.

3. **D-KMP-sample** — `github.com/dbaroncelli/D-KMP-sample` — Daniele Baroncelli's architecture showcase with shared navigation and VM, SwiftUI and Compose renderers. Demonstrates Decompose-style shared navigation (though D-KMP is its own mini-framework).

4. **SKIEDemoSample** — `github.com/touchlab/SKIEDemoSample` — Touchlab's reference for SKIE usage. Small but exhaustive — shows Flow, suspend, sealed classes, enums, default args, all flowing to SwiftUI.

5. **frankois944/kmp-mvvm-exploration** — concrete exploration of shared KMP ViewModel + SwiftUI + Koin injection patterns. Late-2024/2025 active project, useful as a "latest idioms" reference.

6. **Touchlab's `KaMP Kit`** — `github.com/touchlab/KaMPKit` — Touchlab's opinionated starter. Not aggressively maintained for 2025 idioms but canonical for structure.

**Notably absent:** large-name consumer apps rarely open-source their full KMP+SwiftUI repos. Axel Springer, McDonald's, Philips, 9GAG have published *talks and blog posts* confirming SwiftUI-on-KMP in production but no code. The pattern is not controversial; it's the default for teams prioritizing iOS UX.

## Implications for discord-claude-bridge

### Changes needed to /buildapp flow

1. **Template scaffold additions**: `iosApp/` directory with `iosApp.xcodeproj`, `Podfile`, base `iOSApp.swift`, `Info.plist`, `Assets.xcassets`. Shared module gains `src/iosMain/` with `KoinIOS.kt` and per-screen VM factory stubs.
2. **Gradle configuration**: `shared/build.gradle.kts` gains `cocoapods { }` block with `summary`, `homepage`, `ios.deploymentTarget = "15.0"`, `podfile = project.file("../iosApp/Podfile")`, `framework { baseName = "shared"; isStatic = true }`. Add SKIE plugin.
3. **New prompt section for Claude**: iOS UI generation step that runs AFTER shared module is complete. Claude now has a well-defined contract (the shared VMs' public `StateFlow` + intent functions) to generate SwiftUI against.
4. **Build step**: `pod install` + `xcodebuild` instead of CMP iOS's `./gradlew iosDeployIPhoneSimulatorDebug`. Keeps existing TestFlight flow (the `.ipa` output is the same shape).
5. **Smoke-test harness**: iOS simulator boot + `xcodebuild test` of a UI smoke test. Current smoke test already boots simulators for CMP — adjust to point at the `iosApp.xcworkspace` scheme.

### Prompt engineering: dual-UI generation

Claude must produce **two UI implementations** from one design intent. Suggested prompt architecture:

- **Step 1 — Shared contract generation**: Claude designs the feature spec, then produces shared module code (models, use-cases, ViewModels with `StateFlow<UiState>` + sealed `UiState`/`UiEvent` hierarchies + intent methods). Output: a concrete contract.
- **Step 2 — Android/Compose UI generation**: Claude reads the contract, writes Compose screens binding to VMs via `collectAsStateWithLifecycle()`.
- **Step 3 — iOS/SwiftUI UI generation**: Claude reads the *same contract*, writes SwiftUI screens + `@Observable` wrappers. Prompt explicitly demands iOS idioms: `NavigationStack`, `SF Symbols` via `Image(systemName:)`, `.sheet`/`.confirmationDialog`, `.refreshable`, haptic feedback via `UIImpactFeedbackGenerator`, `Dynamic Type` via `Font.body` not hard-coded sizes, `@FocusState` for keyboard, swipe actions on `List`.

The contract-first approach is essential: it prevents two Claude runs from inventing different state shapes.

### Parity + drift risk

- **Same VM contract = same user flows, same loading/error states, same intent surface.** Drift can only occur in UI affordances (which is desired — iOS should feel iOS).
- **Risk vectors:**
  - Claude adding a `SwiftUI`-only screen-level state (say, `@State var searchQuery`) that shadows shared VM state → the two platforms diverge on search behavior.
  - Claude hard-coding strings/numbers on iOS that exist as shared constants.
  - Claude implementing a use-case in SwiftUI (say, debouncing) rather than reusing the shared VM's debounced Flow.
- **Mitigations:**
  - Strict prompt rule: "Swift UI layer MAY NOT introduce new business logic. All async/IO must go through the shared VM's intent methods."
  - A linter/grep pass after generation that flags `URLSession`, `JSONDecoder`, `Timer.publish`, `UserDefaults` in `Screens/` — these indicate leaked business logic.
  - Post-gen diff check: dump shared VM public API, ensure every public intent/state is referenced from at least one SwiftUI screen.

### Testing strategy

- **Shared module tests** (Kotlin test on JVM): cover 100% of VM logic and use-cases. These are the parity contract.
- **Compose UI tests** (Android): smoke-level — does each screen render without crashing for each `UiState` variant?
- **SwiftUI snapshot/preview tests**: `#Preview` blocks for each screen and each VM state (loading/loaded/error/empty). Run through `xcrun simctl` in CI if needed.
- **XCUITest smoke**: one happy-path test per app (launches, navigates to main screen, exercises one intent). Mirrors the existing bot smoke-test pattern.
- **No need for cross-platform UI tests** — if shared VM tests pass and each platform smoke passes, parity is bounded by the contract.

## Phased implementation plan for the bot

### Phase 0 — Spike (1–2 days, manual)
Hand-build one KMP app with SwiftUI iOS + Compose Android + shared VMs + SKIE + Koin. Verify: `pod install` works, iOS simulator boots, `@Observable` adapter receives `StateFlow` updates, Koin resolves, TestFlight build succeeds. Capture exact file contents as template.

### Phase 1 — Template + prompt update
- Add `iosApp/` template to `templates/` dir.
- Add SKIE + Koin + `cocoapods {}` to `shared/build.gradle.kts` template.
- Split Claude prompt into (a) shared-contract step (b) Compose step (c) SwiftUI step.
- Ship behind flag: `/buildapp --ios=swiftui` (default remains CMP during rollout).

### Phase 2 — Build pipeline
- Replace CMP iOS build (`./gradlew ios…`) with `pod install` + `xcodebuild archive` + `xcodebuild -exportArchive`.
- Update `asc_api.py` / TestFlight flow: the `.ipa` export method stays `app-store-connect`.
- Update smoke test harness to boot iOS simulator and run `xcodebuild test` against a generated UI smoke target.

### Phase 3 — Flip the default
Once Phase 2 has run cleanly for ~20 generated apps, make `--ios=swiftui` the default. Retain `--ios=cmp` as an escape hatch for canvas-heavy apps (games, charting) where rewriting in SwiftUI would be painful.

### Phase 4 — Hybrid mode (stretch)
Allow per-screen override in `/buildapp` spec: `screens: [ {name: home, ios: swiftui}, {name: chart, ios: cmp} ]`. Template stitches `ComposeUIViewController` for CMP screens into the SwiftUI `NavigationStack`.

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| SKIE compiler plugin breaks with new Kotlin version | Medium | Pin Kotlin + SKIE versions in template; track SKIE releases (they usually follow Kotlin within days). |
| Claude hallucinates non-existent SwiftUI APIs (iOS-version mismatch) | High | Prompt explicitly pins iOS deployment target (e.g. iOS 17) and provides allow-list of SwiftUI APIs. |
| `pod install` fails in CI / generation environment (Ruby, CocoaPods version) | Medium | Pre-install CocoaPods in bot's container; add health check. Alternative: switch to SPM once Swift Export stabilizes. |
| Dual-UI generation doubles token cost / latency | High | Shared contract is generated once; each UI generation is smaller and parallelizable. Projected cost increase: ~1.4×, not 2×. |
| iOS ViewModel wrapper leaks memory (Task not cancelled) | Medium | Standard `deinit { task?.cancel() }` pattern is in template; add lint rule. |
| Koin reified resolution bugs from Swift | Low | Per-VM factory functions avoid it entirely. |
| Shared VM exposes Kotlin-only types (Unit, Pair, Result) that Swift can't consume well | Medium | SKIE handles Pair/Unit; for `Result`, wrap in custom sealed class. Lint the shared public API. |
| Sealed class + SKIE: exhaustiveness requires all cases open to Swift | Low | SKIE handles this; just requires not using `Nothing` subtypes directly. |
| User expects hot-reload iOS dev loop (Compose has preview + JVM tests; SwiftUI has Previews) | Low | SwiftUI `#Preview` is great; document in generated README. |
| Xcode project file format drift causes generated `.xcodeproj` to break on new Xcode version | Medium | Generate minimal project; consider `xcodegen`/`tuist` for reproducibility; or leave it and accept annual maintenance. |

## Open questions

These need a prototype before committing:

1. **Can Claude reliably produce correct SwiftUI code at scale?** Compose is well-represented in training data; SwiftUI even more so, but idiomatic iOS 17+ `@Observable` + `NavigationStack` + `.searchable` code is newer. Needs evaluation on 5–10 real app specs before flipping default.
2. **Single-repo Gradle+Xcode build determinism**. Does `pod install` + `xcodebuild` consistently succeed inside the bot's execution environment (Docker? bare macOS host)?
3. **Does SKIE work cleanly with the current Kotlin version the bot uses?** Check `templates/` for Kotlin version and cross-check SKIE compatibility matrix.
4. **Do we want `xcodegen` / `tuist`?** An `.xcodeproj` that's generated each build is cleaner but adds a dependency. Alternative: ship a static `.xcodeproj` in the template and never regenerate it.
5. **What about iPad/visionOS?** Generated apps so far target iPhone; SwiftUI naturally adapts to iPad. visionOS is a free bonus with SwiftUI that CMP cannot offer. Worth mentioning in marketing but don't over-invest in layout work.
6. **Dynamic Type / accessibility audit automation**. Can we run a post-gen accessibility check (e.g. all `Image`s have labels, no hard-coded `.font(.system(size: 14))`)? Would be a prompt rule and a lint script.
7. **Design system consistency**. Should the bot output a shared design token file (colors, spacing) consumed by both Compose `Theme` and SwiftUI `Color.appPrimary`? Most real teams do. Prevents visual drift.

## Sources

- [iOS integration methods — Kotlin Multiplatform Documentation](https://kotlinlang.org/docs/multiplatform/multiplatform-ios-integration-overview.html)
- [SKIE — Swift Kotlin Interface Enhancer (Touchlab)](https://skie.touchlab.co/)
- [SKIE Features — Flows](https://skie.touchlab.co/features/flows)
- [SKIE Combine preview](https://skie.touchlab.co/features/combine)
- [touchlab/SKIE GitHub](https://github.com/touchlab/SKIE)
- [touchlab/SKIEDemoSample](https://github.com/touchlab/SKIEDemoSample)
- [Kotlin Coroutines and Swift, revisited — Touchlab](https://touchlab.co/kotlin-coroutines-swift-revisited)
- [rickclephas/KMP-NativeCoroutines](https://github.com/rickclephas/KMP-NativeCoroutines)
- [rickclephas/KMP-ObservableViewModel](https://github.com/rickclephas/KMP-ObservableViewModel)
- [joreilly/PeopleInSpace](https://github.com/joreilly/PeopleInSpace)
- [joreilly/Confetti](https://github.com/joreilly/Confetti)
- [John O'Reilly: Using KMM-ViewModel to share VM between iOS and Android](https://johnoreilly.dev/posts/kmm-viewmodel/)
- [John O'Reilly: Consuming Compose for iOS in a SwiftUI application](https://johnoreilly.dev/posts/swiftui-compose-ios/)
- [dbaroncelli/D-KMP-sample](https://github.com/dbaroncelli/D-KMP-sample)
- [frankois944/kmp-mvvm-exploration](https://github.com/frankois944/kmp-mvvm-exploration)
- [touchlab/KMMBridge](https://github.com/touchlab/KMMBridge)
- [KMMBridge SPM docs](https://kmmbridge.touchlab.co/docs/spm/IOS_SPM/)
- [Set up ViewModel for KMP — Android Developers](https://developer.android.com/kotlin/multiplatform/viewmodel)
- [Koin — KMP Advanced Patterns](https://insert-koin.io/docs/reference/koin-mp/kmp/)
- [KMP Bits — Koin Injection on iOS Without Reified Crashes](https://www.kmpbits.com/posts/koin-inject-kmp)
- [KMP Bits — StateFlow & SharedFlow in KMP](https://www.kmpbits.com/posts/stateflow-kmp)
- [Crossing the Finish Line: StateFlow & SharedFlow in Kotlin Multiplatform](https://www.kmpbits.com/posts/stateflow-kmp)
- [Kotlin Multiplatform SwiftUI integration — JetBrains](https://kotlinlang.org/docs/multiplatform/compose-swiftui-integration.html)
- [Swift package export setup — Kotlin docs](https://kotlinlang.org/docs/multiplatform/multiplatform-spm-export.html)
- [Adding Swift packages to KMP — Kotlin docs](https://kotlinlang.org/docs/multiplatform/multiplatform-spm-import.html)
- [Compose Multiplatform 1.8.0 Released — JetBrains Blog](https://blog.jetbrains.com/kotlin/2025/05/compose-multiplatform-1-8-0-released-compose-multiplatform-for-ios-is-stable-and-production-ready/)
- [Getting the native iOS look & feel in CMP — jacobras](https://medium.com/@jacobras/getting-the-native-ios-look-feel-in-your-compose-multiplatform-app-33371e6ad362)
- [Kotlin Multiplatform – Bridging Compose & iOS UI Frameworks — Infinum](https://infinum.com/blog/kotlin-multiplatform-swiftui/)
- [Native at heart: Mixing SwiftUI with Compose Multiplatform — Sammuigai](https://sammuigai880.medium.com/native-at-heart-mixing-swiftui-with-compose-multiplatform-b50b51f1ba6d)
- [KMP: Mobile Development with Shared Code & Improved Swift UI Integration — Axel Springer Tech](https://medium.com/axel-springer-tech/kotlin-multiplatform-mobile-development-with-shared-code-and-improved-swift-ui-integration-d39e2b25e066)
- [Kotlin Multiplatform Development Roadmap for 2025 — JetBrains Blog](https://blog.jetbrains.com/kotlin/2024/10/kotlin-multiplatform-development-roadmap-for-2025/)
- [Kotlin to Swift Export: Native iOS Integration Guide 2025 — kmpship](https://www.kmpship.app/blog/kotlin-swift-export-ios-integration-2025)
- [KMP Navigation: Decompose vs Voyager vs native — droidcon](https://www.droidcon.com/2024/04/09/navigating-the-waters-of-kotlin-multiplatform-exploring-navigation-solutions/)
- [Decompose Samples](https://arkivanov.github.io/Decompose/samples/)
- [Using Kotlin Multiplatform With KMMBridge and SKIE to Publish a Native Swift SDK — PowerSync](https://www.powersync.com/blog/using-kotlin-multiplatform-with-kmmbridge-and-skie-to-publish-a-native-swift-sdk)
- [compose-multiplatform issue #4902 — Scaffold scrolls improperly when keyboard opens on iOS](https://github.com/JetBrains/compose-multiplatform/issues/4902)
- [compose-multiplatform issue #3621 — Keyboard and TextField issues on iOS](https://github.com/JetBrains/compose-multiplatform/issues/3621)
- [compose-multiplatform issue #5026 — Gesture conflicts on iOS](https://github.com/JetBrains/compose-multiplatform/issues/5026)
