# Bot Todos

- [ ] `/buildapp` data modeling interview — conversational flow that helps non-engineers define backend data models from their UX description, then auto-provisions Supabase tables and wires up the Kotlin data layer
- [x] add save states — `/save`, `/save list`, `/save undo`, `/save redo`, `/save github`
- [x] Android + Web auto-fix pipelines — crash detection, health checks, and auto-fix loops matching iOS

### Lovable-inspired features (competitive parity + differentiation)

**High priority — biggest impact, feasible in Discord:**

- [ ] **Screenshot-to-UI** — user uploads a screenshot or design mockup in Discord, bot interprets the image and generates matching KMP UI code. Lovable's killer onboarding feature. Discord already supports image attachments, and Claude is multimodal — wire the image URL into the prompt
- [ ] **Auto-preview after edits** — after every `@workspace` prompt completes, automatically take a screenshot and send it in chat. Mimics Lovable's live preview panel — users see what changed without running `/demo`
- [ ] **App template gallery** — curated starting points beyond the blank KMP template. `/buildapp` could offer a template picker or auto-select based on the description. Lovable has design templates on paid plans. Initial templates:
  - **Bachelor/bachelorette party** — itinerary/schedule, venue map + addresses, attendee list, packing list, countdown, photo wall (Supabase storage). Perfect showcase: non-technical organizer builds a real native app in minutes, shares via TestFlight
  - **Event/conference** — schedule with filtering, venue map, speaker bios, push notifications
  - **Social feed** — posts, likes, comments, user profiles (Supabase realtime)
  - **E-commerce** — product catalog, cart, Stripe checkout
  - **Dashboard** — charts, metrics, data tables
  - **Game** — Compose Canvas-based simple game

**Medium priority — strong differentiators:**

- [ ] **TestFlight invite automation** — `/testflight invite email@example.com` adds testers via App Store Connect API. Completes the "build → share with friends" flow entirely from Discord. Currently users have to manually add emails in App Store Connect. Bot should send a friendly explainer: "Your friends just need the free TestFlight app (one-time install), then they tap the invite link — no App Store approval needed, they'll have it in 2 minutes"
- [ ] **Android APK sharing** — `/share android` uploads the built APK to a download link (GitHub release or direct Discord attachment) and gives a URL to text friends. Zero friction — no app store, no review, just install
- [ ] **TestFlight guided setup** — `/testflight setup` walks non-technical users through Apple Developer account + API key setup entirely within Discord. Step-by-step with buttons, file upload for .p8 key, text input for credentials. Bot stores keys securely and auto-creates the App Store Connect app record via API. Goal: a non-engineer goes from zero to "ready to ship" in 10 minutes without touching a terminal
- [ ] **App Store readiness check** — `/appstore check` evaluates the app against common Apple rejection reasons (4.2 minimum functionality, placeholder content, missing privacy policy, no app icon) and warns before the user wastes time submitting. Includes a disclaimer that Apple has restrictions and we're streamlining the process
- [ ] **Public web deploy** — `/deploy web` pushes the WASM build to Netlify/Vercel/Cloudflare Pages and returns a permanent public URL. Nice-to-have since KMP already builds web as a side effect — free "try my app" sharing link before TestFlight is set up
- [ ] **Security scan** — `/security` command that checks for exposed API keys in source, missing Supabase RLS policies, hardcoded secrets, and common OWASP issues. Lovable's scanner only checks if RLS exists (not if it's correct) — we can do better by actually validating policy logic
- [ ] **Stripe/payments integration** — `/addpayments` or detect payment intent in `/buildapp` description and auto-scaffold Stripe checkout (KMP + Supabase Edge Function). Lovable has native Stripe integration; for native apps this is even more valuable
- [ ] **Before/after screenshots on save** — when user runs `/save`, capture a screenshot and store it with the save point. `/save list` shows thumbnails. Makes versioning visual like Lovable's "Versioning 2.0"
- [ ] **Build status embed** — after `/buildapp`, send a rich Discord embed showing: platforms built, build times, fix attempts, screenshot thumbnails, web link, TestFlight status. One glanceable summary instead of a stream of messages
- [ ] **Suggested next features** — after a successful `/buildapp` or `/demo`, analyze the app and suggest 3-4 natural next features ("Add dark mode", "Add push notifications", "Add user profiles") as Discord buttons. Lovable does this implicitly through chat; we can be more proactive

**Lower priority — nice to have, or blocked by Discord:**

- [ ] **Figma import** — accept Figma export (SVG/PNG) uploads and generate matching KMP UI. Lower fidelity than Lovable's Builder.io plugin but still useful for designers sharing mockups
- [ ] **Google Play internal testing** — `/deploy android play` uploads AAB to Google Play internal testing track (mirrors TestFlight for Android)
- [ ] **Project health dashboard** — expand `/dashboard` web page to show: build history, recent changes, deploy status, cost per project, fix log timeline. Lovable has built-in analytics; this is our equivalent
- [ ] **MCP server support** — connect to external tools (Notion, Linear, Jira) for pulling requirements or syncing project state. Lovable added MCP in 2.0; useful for teams
- [ ] **Visual editor via web** — when standalone chat UI ships, add a click-to-edit overlay on the web preview (like Lovable's Figma-like visual editor). Blocked until we have our own web frontend

### Multi-user (see README for full list)

- [ ] Per-user GitHub tokens for `/save github`
- [ ] OAuth onboarding for third-party keys (Supabase, Apple, Google, Claude API)
- [ ] LLM selection (let users choose provider + manage subscriptions)
- [ ] Multi-user workspace isolation
- [ ] Per-user spend tracking
- [ ] Per-user Claude sessions

Consider allowing branching in a future development, allowing non engineers easily understand how branching works
