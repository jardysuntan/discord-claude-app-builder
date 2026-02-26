# discord-claude-app-builder

A Discord bot that builds cross-platform apps from natural language. Describe what you want, and it scaffolds a Kotlin Multiplatform project, has Claude Code write the code, auto-fixes build errors, and demos the result on Android, iOS, and Web — all from your phone.

<table>
  <tr>
    <td align="center">
      <img src="https://github.com/user-attachments/assets/4d1a97e5-fe18-4b71-97bd-db494078c1be" width="350"/>
    </td>
    <td align="center">
      <img src="https://github.com/user-attachments/assets/db6b4b82-d410-4bcb-9f66-3b0f5fcfe6b9" width="350"/>
    </td>
    <td align="center">
      <img src="https://github.com/user-attachments/assets/a99cb18f-871e-4551-8b48-baa6f7f8cd10" width="350"/>
    </td>
       <td align="center">
      <img src="https://github.com/user-attachments/assets/1259c341-12c7-4128-8c3e-140cbc779570" width="350"/>
    </td>
  </tr>


</table>

---

## Getting Started

You don't need to know how to code. Once the bot is running (see [Setup](#setup) below), everything happens through Discord messages.

### Build your first app

DM the bot:

```
/buildapp a pomodoro timer with task categories
```

That's it. Wait a few minutes and you'll get:
- A screenshot of your app running on Android
- A video recording of it in action
- A web link you can open on your phone right now
- An iOS build ready for the simulator (or TestFlight)

### Talk to your app

Every app gets its own workspace. Use `@appname` to tell Claude what to change:

```
@pomodoro add a dark mode toggle
@pomodoro make the timer bigger and use a circular progress bar
@pomodoro add sound effects when the timer ends
```

Then rebuild to see the changes:

```
/demo web
/demo android
/demo ios
```

### What you can say

| Command | What it does |
|---------|-------------|
| `/buildapp <describe anything>` | Build a full app from a description |
| `@appname <request>` | Tell Claude to change your app |
| `/demo android` or `ios` or `web` | See your app running |
| `/fix` | Auto-fix build errors (Claude reads the error and fixes it) |
| `/deploy ios` | Install directly on your iPhone |
| `/testflight` | Upload to TestFlight so anyone can install it |
| `/widget <description>` | Add an iOS home screen widget |
| `/vid` | Record a video of the Android app |
| `/dashboard` | A launcher page showing all your apps |
| `/ls` | See all your projects |
| `/use <appname>` | Switch to a different project |
| `/fixes` | See what build errors Claude has fixed so far |
| `/help` | Full command list |

### Tips for better results

- **Be specific.** "A workout tracker with exercise categories, sets/reps logging, and a rest timer" works way better than "a fitness app."
- **Iterate in small steps.** Build the core idea first, then layer on features one at a time with `@appname`.
- **Let the bot fix itself.** If something breaks, the bot automatically tries to fix it. You can also run `/fix` with extra instructions like `/fix use Material 3 colors`.
- **Check the fix log.** `/fixes` shows you every error Claude has encountered and how it fixed it — the bot remembers these so it doesn't make the same mistake twice.

### Typical flow from your phone

1. `/buildapp a workout tracker` — wait a few minutes, get screenshots + web link
2. Open the web link on your phone to try it out
3. `@workouttracker add a rest timer between sets` — Claude modifies the code
4. `/demo web` — see the update instantly
5. `/testflight` — upload so friends can install it on their iPhones

---

## How it works

You DM the bot something like `/buildapp a habit tracker with streaks`. Behind the scenes:

1. A project gets scaffolded from a template
2. Claude Code writes all the UI and logic
3. The bot builds for Android — if it fails, Claude reads the errors and fixes them (up to 8 attempts)
4. Once it compiles, you get a screenshot and video from the Android emulator
5. It builds for Web and gives you a link to try the app in your browser
6. It builds for iOS — if it fails, Claude fixes those too, including crash-on-launch detection
7. `/testflight` uploads to TestFlight so anyone can install it natively

You get real-time progress updates in Discord as Claude works.

**Build fix memory:** Every error and fix is logged per project. The next time a build fails, Claude sees what went wrong before and avoids repeating mistakes.

---

## Setup

### Prerequisites

- macOS (needed for iOS builds; Android + Web work on Linux too)
- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Android SDK with an AVD configured (for Android builds/demos)
- Xcode (for iOS builds — optional, can add later)
- Node.js + PM2 (`npm install -g pm2`)
- A Discord bot token ([create one here](https://discord.com/developers/applications))
- Tailscale (optional, for remote access from your phone)

### Install

```bash
git clone https://github.com/jardysuntan/discord-claude-app-builder.git
cd discord-claude-app-builder

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your values (see below)
```

### Configure `.env`

The important ones:

```bash
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_ALLOWED_USER_ID=your-discord-user-id   # only you can use build commands

BASE_PROJECTS_DIR=~/Projects                     # where apps get created
CLAUDE_BIN=claude                                # path to claude CLI
ANDROID_AVD=Pixel_9_Pro_XL                       # your emulator name
TAILSCALE_HOSTNAME=100.x.x.x                    # for remote web demo access
```

See `.env.example` for all options (Android, iOS, Web, scrcpy, etc).

### KMP template (recommended)

Generate a template from [kmp.jetbrains.com](https://kmp.jetbrains.com) and place it at:

```
templates/kmp/KMPTemplate/
```

The bot will copy this for each new project. Without it, `commands/create.py` generates a minimal structure.

### Run

```bash
# With PM2 (recommended — auto-restarts on file changes)
pm2 start ecosystem.config.cjs
pm2 logs discord-claude-bridge

# Or directly
python3 bot.py
```

---

## All Commands

| Command | What it does |
|---------|-------------|
| `/build app <description>` | Full pipeline: scaffold + build + demo all platforms |
| `/create <AppName>` | Just scaffold a project (no build) |
| `/build android\|ios\|web` | Build for a specific platform |
| `/demo android\|ios\|web` | Build + launch + screenshot |
| `/deploy ios\|android` | Install on a physical device |
| `/testflight` | Archive + upload to TestFlight |
| `/fix [instructions]` | Auto-fix build errors with Claude |
| `/widget <description>` | Add iOS home screen widget |
| `/vid` | Record a video from the Android emulator |
| `/deleteapp <name>` | Remove a project |
| `/rename <old> <new>` | Rename a workspace |
| `/queue task1 --- task2 --- ...` | Queue tasks for sequential execution |
| `/spend` | Check today's spend and remaining budget |
| `@workspace <prompt>` | Send a prompt to Claude in that project |
| `/run <cmd>` | Run a shell command in the workspace |
| `/status` `/diff` `/commit` `/pr` | Git workflow |
| `/ls` `/use` `/where` | Workspace management |
| `/dashboard` | Web launcher for all apps |
| `/mirror start\|stop` | Android emulator in browser |
| `/showcase <ws>` | Share a demo publicly |
| `/fixes` `/fixes clear` | View or clear the build fix log |
| `/memory show\|pin\|reset` | Project memory (CLAUDE.md) |
| `/newsession` | Reset Claude session |
| `/maintenance [msg\|off]` | Toggle maintenance mode (owner only) |
| `/announce <msg>` | Post to announcement channel (owner only) |
| `/setup` | Check setup status |
| `/help` | Full command reference |

---

## Developer Reference

Everything below is for working on the bot itself.

### Tech stack

- **Python 3.10+** with **discord.py** — the bot itself (uses match/case)
- **Claude Code CLI** — AI that writes and fixes the app code
- **Kotlin Multiplatform + Compose Multiplatform** — one codebase, three platforms
- **Gradle** — builds Android (APK) and Web (WASM)
- **Xcode** — builds iOS (simulator + physical device)
- **PM2** — keeps the bot running, auto-restarts on code changes
- **Tailscale** — lets you access web demos and Android mirrors from your phone
- **ws-scrcpy** (optional) — browser-based Android emulator interaction

### Architecture

```
Discord DM → parser.py → bot.py → handler
                                      ↓
                              claude_runner.py ←→ Claude Code CLI
                                      ↓
                              agent_loop.py (build → error → fix → retry)
                                      ↓
                              platforms.py (gradle / xcodebuild / wasm)
                                      ↓
                              Screenshots, videos, web server, device install
```

Key design decisions:
- **Claude sessions persist per workspace** — context carries over between prompts
- **Stream-json output** — real-time progress updates in Discord as Claude works (with friendly labels)
- **Auto-fix loop** — build errors get fed back to Claude automatically (up to 8 retries)
- **Crash detection** — iOS demos detect crash-on-launch and auto-fix runtime errors
- **Fix memory** — `.fixes.md` logs every error+fix per workspace; injected into future fix prompts so Claude learns from past mistakes
- **Safety checks** — `/run` and `/runsh` have an allowlist; dangerous commands are blocked
- **Maintenance mode** — owner can block public commands while updating the bot

### Project structure

```
bot.py                  # Entry point — Discord client, message routing
parser.py               # Message grammar — slash commands + @workspace prompts
config.py               # Environment variables
platforms.py            # Build/install/demo for Android, iOS, Web
claude_runner.py        # Claude Code CLI invocation with session continuity
agent_loop.py           # Auto-fix loop: build → error → Claude fix → rebuild
workspaces.py           # Workspace registry (JSON-backed)
commands/
  buildapp.py           # /buildapp — full pipeline
  create.py             # /create — scaffold KMP project
  fix.py                # /fix — auto-fix build errors
  fixes_cmd.py          # /fixes — persistent build fix log
  widget.py             # /widget — iOS WidgetKit
  testflight.py         # /testflight — archive + upload to TestFlight
  dashboard.py          # /dashboard — web launcher page
  bot_todo.py           # /bot-todo — internal todo list
  memory_cmd.py         # /memory — project memory (CLAUDE.md)
  queue.py              # /queue — batch task queue
  git_cmd.py            # Git commands (/status, /diff, /commit, /pr, etc.)
  run_cmd.py            # /run, /runsh — terminal commands
  showcase.py           # /showcase, /tryapp — public demos
  scrcpy.py             # /mirror — Android emulator in browser
templates/
  kmp/KMPTemplate/      # KMP project template (copied for each new app)
```

### Adding a new command

1. **Parser** (`parser.py`): Add a case in the `match cmd:` block
2. **Handler** (`commands/yourcommand.py`): Create the handler function
3. **Bot routing** (`bot.py`): Add import + case in the `match cmd.name:` block
4. **Help text** (`bot.py`): Update `help_text()` function

### Key patterns

- **Status callbacks**: Handlers take `on_status: Callable[[str, Optional[str]], Awaitable[None]]` — first arg is message text, second is optional file path
- **Build results**: `BuildResult(success, output, error)`, `DemoResult(success, message, screenshot_path)`, `DeployResult(success, message)`
- **Agent loop**: `run_agent_loop()` handles the build-error-fix cycle for any platform
- **Claude sessions**: Persist per workspace via `ClaudeRunner._sessions` dict — context carries over between prompts

### Environment setup for all platforms

**Android** (required):
```bash
# Install Android Studio → SDK Manager → install SDK + emulator
# Create an AVD (e.g., Pixel 9 Pro XL, API 35)
export ANDROID_AVD=Pixel_9_Pro_XL
```

**iOS** (requires macOS + Xcode):
```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
# Open Xcode → Settings → Platforms → install iOS simulator
```

**TestFlight** (requires Apple Developer Program, $99/year):
```bash
# App Store Connect → Users & Access → Integrations → API Keys
export APPLE_TEAM_ID=your-team-id
export ASC_KEY_ID=your-key-id
export ASC_ISSUER_ID=your-issuer-id
# Place .p8 file at ~/.private_keys/AuthKey_<KEY_ID>.p8
```

**Web**: Works out of the box — just needs Gradle (bundled with KMP template).

**Tailscale** (optional, for remote access):
```bash
# Install Tailscale on your Mac and phone
export TAILSCALE_HOSTNAME=100.x.x.x
# Now web demos and mirror are accessible from your phone anywhere
```

## What's next

- [ ] **Multi-user support** — let others build apps too (currently owner-only for builds)
- [ ] **Automated TestFlight tester invites** — bot adds testers via App Store Connect API
- [ ] **Android crash detection** — match the iOS crash-detect-and-fix flow for Android demos

## License

MIT
