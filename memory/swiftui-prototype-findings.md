---
name: SwiftUI + KMP prototype — findings
description: End-to-end verification that SwiftUI can consume a KMP workspace via SKIE. Works. Red flags + next steps captured.
type: project
---

# SwiftUI + KMP Prototype — Findings (2026-04-05)

## TL;DR

**It works end-to-end.** Forked `YangzihesobachMobile` → `YangzihesobaSwiftUIProto`, added SKIE, built a SwiftUI screen that observes `StateFlow<AppConfig>` alongside the existing Compose UI in a `TabView`. iOS build succeeded, simulator rendered the SwiftUI List with live data from the Kotlin `ConfigRepository` singleton. Total edits: ~3 Gradle lines + 1 Swift file (~130 lines).

Screenshot of working prototype: `/tmp/swiftui-proto-shot.png` (showed real data: Version 1, JoinCode `YANGZI26`, 15 guests, 15 events, 12 venues, in native iOS List/NavigationStack/TabBar).

## What was built

```
/Users/jaredtanpersonal/Projects/discord-claude-bot-apps/YangzihesobaSwiftUIProto/
├── gradle/libs.versions.toml          ← added skie = "0.9.5" + plugin
├── composeApp/build.gradle.kts        ← added alias(libs.plugins.skie)
└── iosApp/iosApp/ContentView.swift    ← rewrote with TabView + SwiftUI screen
```

The rest of the workspace is **untouched** — Android build, Web build, commonMain Kotlin code, iosMain platform shims all unchanged.

## Architecture that worked

```
┌─ iosApp/ (Swift) ───────────────────────────┐
│                                             │
│  TabView                                    │
│  ├── SwiftUISummaryScreen                   │
│  │   └── AppConfigViewModel (ObservableObj) │
│  │       └── observes Kotlin StateFlows     │
│  │           via SKIE for-await loops       │
│  │                                          │
│  └── ComposeScreen                          │
│      └── MainViewControllerKt (unchanged)   │
│          └── existing Compose app           │
└──────────────────────────────────────────────┘
              ↓ SKIE-generated bridge
┌─ composeApp/ (Kotlin commonMain) ───────────┐
│  AppDependencies (singleton object)         │
│  └── ConfigRepository                       │
│      ├── val config: StateFlow<AppConfig>   │
│      ├── val isLoading: StateFlow<Boolean>  │
│      ├── val error: StateFlow<String?>      │
│      └── suspend fun loadConfig()           │
└──────────────────────────────────────────────┘
```

**Swift side pattern that worked:**
```swift
@MainActor
final class AppConfigViewModel: ObservableObject {
    @Published var guestCount: Int = 0
    // ...
    private let repo = AppDependencies.shared.configRepository

    func start() async {
        let configFlow = repo.config
        await withTaskGroup(of: Void.self) { group in
            group.addTask { @MainActor [weak self] in
                for await value in configFlow {
                    self?.guestCount = Int(value.guests.count)
                    // ...
                }
            }
            // ... two more tasks for isLoading + error
        }
    }
}
```

**SwiftUI view pattern:**
```swift
struct SwiftUISummaryScreen: View {
    @StateObject private var viewModel = AppConfigViewModel()
    var body: some View {
        NavigationStack { List { ... } }
            .task { await viewModel.start() }
            .refreshable { viewModel.refresh() }
    }
}
```

## Green flags ✅

1. **SKIE is drop-in.** 3 lines of Gradle config. No changes to Kotlin code needed.
2. **Kotlin `StateFlow<T>` → Swift `for await`** just works. SKIE generates an `AsyncSequence` conformance.
3. **Kotlin `object` singletons** (like `AppDependencies`) expose as `ClassName.shared` in Swift automatically.
4. **Kotlin `suspend fun`** exposed as Swift `async throws` automatically (used for `repo.loadConfig()`).
5. **Native iOS feel is immediate** — NavigationStack with `.large` title, List sections with iOS chrome, TabView with SF Symbols (`sparkles`, `square.stack.3d.up`), pull-to-refresh. All felt like real iOS, not emulated.
6. **Progressive adoption works** — Compose screens and SwiftUI screens coexist in the same app via TabView. No need for a big-bang rewrite.
7. **Shared ViewModel pattern is viable** — the Kotlin `ConfigRepository` becomes the shared state source, Swift `ObservableObject` is a thin presentation layer.
8. **Build is fast** — `linkDebugFrameworkIosSimulatorArm64` took 49s first run (cold), Swift compile was <5s, xcodebuild was <3s incremental.

## Red flags / gotchas 🚩

1. **Xcode version mismatch warning.** Kotlin 2.0.21 tested max against Xcode 16.0; system has 26.2. Gradle printed:
   > The selected Xcode version (26.2) is higher than the maximum known to the Kotlin Gradle Plugin. Stability in such configuration hasn't been tested.
   
   Build succeeded, but this is a known-unknown. May need `kotlin.apple.xcodeCompatibility.nowarn=true` in `gradle.properties`, OR upgrade to Kotlin 2.1.x + SKIE 0.10.x.

2. **SKIE rename collisions.** SKIE auto-renamed 4 Kotlin `description` properties → `description_` (because ObjC base class has `description()`). Also renamed `Ui_unitDensity.toSp` → `toSp_`. Swift code accessing these Kotlin types must use the renamed form. Workarounds:
   - Add `@ObjCName("description")` annotations in Kotlin (requires opt-in)
   - Rename in Kotlin (Contest.description → Contest.summary etc.)
   - Suppress via `SuppressSkieWarning.NameCollision` if willing to live with `_` suffixes
   
   **Impact on bot:** Claude will need to know about this when generating Kotlin models. Either prompt Claude to avoid property names that collide with ObjC (`description`, `hash`, `debugDescription`), or have a post-gen step that adds `@ObjCName` annotations.

3. **Swift 6 concurrency strictness.** First attempt using `async let` with closure-captured `repo` failed with "MainActor-isolated property can not be mutated from a nonisolated context." Required switching to `withTaskGroup` + explicit `@MainActor` annotation on each task. Claude writing this kind of code from scratch may trip on it.

4. **Error: `Cannot infer a bundle ID from packages of source files`** — warning (not failure) during framework link. Works but should pass `-Xbinary=bundleId=com.jaredtan.yangzihesobachmobile` in Gradle.

5. **Kotlin `Boolean` → Swift `Bool` conversion** needed `.boolValue` (SKIE wraps in `KotlinBoolean`). Not ideal but one-line fix each time.

6. **Build script phase has no outputs declared** — iOS build reruns `./gradlew embedAndSignAppleFrameworkForXcode` every single incremental build. Slow. Should declare outputs to get proper build caching.

## Estimated effort to fully migrate one screen

**HomeScreen (simpler Compose screen) → SwiftUI equivalent:** ~4-8 hours by hand, mostly writing SwiftUI layout + learning the Kotlin data shapes. The state-interop boilerplate is template-able.

**Per-screen conversion cost (after first one):** probably 2-4 hours each for experienced SwiftUI dev, maybe 1-2h with Claude assistance once the pattern is nailed.

## Implications for the discord-claude-bridge bot

1. **Template the iosApp/ContentView.swift pattern.** The TabView shell + `@MainActor ObservableObject` + `withTaskGroup` of `for await` loops is mechanical — Claude can template it.

2. **Generate dual-UI prompts.** When adding a feature, Claude would need to write:
   - Kotlin `ViewModel` in commonMain exposing `StateFlow<State>` + suspend fns for actions (reusable across Android/iOS/Web)
   - Compose `@Composable fun XScreen(vm: XViewModel)` (for Android + Web via CMP)
   - SwiftUI `struct XScreen: View` observing a Swift `ObservableObject` wrapping the Kotlin VM (for iOS)

3. **Add a `description`-collision linter** to the bot's preflight fixes — auto-rename Kotlin fields or inject `@ObjCName` when SKIE export is enabled.

4. **Xcode compat pin.** Either upgrade to Kotlin 2.1.x + SKIE 0.10.x across the board, OR silence the warning explicitly. Recommend upgrade when user bandwidth allows.

5. **Bot command design (future work).** A `--ui=swiftui-ios` flag on `/buildapp` that:
   - Swaps in the SwiftUI scaffold for `iosApp/`
   - Adds SKIE Gradle config
   - Runs Claude with a prompt that generates dual-UI
   - Post-gen lint pass for ObjC-collision names

## Recommendation

**Ship this as an opt-in path, don't flip the default yet.** Reasoning:
- Current CMP-iOS flow works and is well-tested across ~10 apps
- SwiftUI+KMP adds dual-UI code-gen complexity that Claude hasn't been exercised on at scale
- Need 3-5 SwiftUI-path apps generated before we have confidence in prompt quality
- The Xcode compat warning is a real unknown — upgrading Kotlin fleet-wide is a separate de-risking exercise

**Next bite-sized experiment:** pick one simpler screen (HomeScreen or SettingsScreen) and manually port it to SwiftUI in this proto workspace. Measure: does the iOS app "feel right"? Is the shared-VM pattern comfortable? Does navigation-between-SwiftUI-screens work as expected with NavigationStack? That's another 1-2 hour experiment and would give enough data to design the bot command.

## Files/paths

- Prototype workspace: `/Users/jaredtanpersonal/Projects/discord-claude-bot-apps/YangzihesobaSwiftUIProto/`
- Original (untouched): `/Users/jaredtanpersonal/Projects/discord-claude-bot-apps/YangzihesobachMobile/`
- Working screenshot: `/tmp/swiftui-proto-shot.png`
- Built .app: `YangzihesobaSwiftUIProto/iosApp/build/DD/Build/Products/Debug-iphonesimulator/iosApp.app`
- SKIE docs: https://skie.touchlab.co/
- Planning doc (earlier research): `memory/swiftui-prototype-planning.md`
