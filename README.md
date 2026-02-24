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
  </tr>

</table>

## How it works

You DM the bot something like `/buildapp a habit tracker with streaks`. Behind the scenes:

1. A KMP project gets scaffolded from a template (or generated from scratch)
2. Claude Code CLI writes all the Compose Multiplatform UI and logic
3. The bot builds for Android — if it fails, Claude reads the errors and fixes them (up to 4 attempts)
4. Once it compiles, you get a screenshot and video from the Android emulator
5. It builds for Web (WASM) and gives you a link to play with the app in your browser
6. It attempts an iOS simulator build if Xcode is available

The whole thing takes a few minutes. You get real-time progress updates in Discord as Claude reads files, writes code, and runs commands.

## Tech stack

- **Python 3.9+** with **discord.py** — the bot itself
- **Claude Code CLI** — AI that writes and fixes the app code
- **Kotlin Multiplatform + Compose Multiplatform** — one codebase, three platforms
- **Gradle** — builds Android (APK) and Web (WASM)
- **Xcode** — builds iOS (simulator + physical device)
- **PM2** — keeps the bot running, auto-restarts on code changes
- **Tailscale** — lets you access web demos and Android mirrors from your phone
- **ws-scrcpy** (optional) — browser-based Android emulator interaction

## Setup

### Prerequisites

- macOS (needed for iOS builds; Android + Web work on Linux too)
- Python 3.9+
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

## Usage

All interaction happens via Discord DMs with the bot.

### Build an app from scratch

```
/buildapp a pomodoro timer with task categories
```

This scaffolds, builds, and demos everything. You'll get screenshots, a video, and a web link.

### Work on an existing project

```
/use myapp                          # set active workspace
@myapp add a settings screen        # send prompts to Claude
/build android                      # rebuild a specific platform
/demo web                           # serve the web build
/fix use Material 3 colors          # auto-fix with instructions
```

### Commands

| Command | What it does |
|---------|-------------|
| `/buildapp <description>` | Full pipeline: scaffold + build + demo all platforms |
| `/create <AppName>` | Just scaffold a KMP project |
| `/build android\|ios\|web` | Build for a specific platform |
| `/demo android\|ios\|web` | Launch and demo (emulator/browser) |
| `/deploy ios\|android` | Install on a physical device |
| `/fix [instructions]` | Auto-fix build errors with Claude |
| `/vid` | Record a video from the Android emulator |
| `/deleteapp <name>` | Remove a project and its workspace |
| `/queue task1 --- task2 --- ...` | Queue tasks for sequential execution with daily budget |
| `/spend` | Check today's spend and remaining budget |
| `@workspace <prompt>` | Send a prompt to Claude in that project |
| `/run <cmd>` | Run a command in the workspace directory |
| `/status` `/diff` `/commit` `/pr` | Git workflow |
| `/ls` `/use` `/where` | Workspace management |
| `/mirror start\|stop` | Start ws-scrcpy for Android mirroring |
| `/showcase <ws>` | Share a demo publicly in a channel |
| `/help` | Full command reference |

### Typical flow from your phone

1. `/buildapp a workout tracker` — wait a few minutes, get screenshots + web link
2. Open the web link on your phone to try the app
3. `@workouttracker add a rest timer between sets` — Claude modifies the code
4. `/build web` then `/demo web` — see the update
5. `/deploy ios` — install directly on your iPhone (needs Xcode + provisioning)

## Architecture

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
- **Stream-json output** — real-time progress updates in Discord as Claude works
- **Auto-fix loop** — build errors get fed back to Claude automatically (up to 4 retries)
- **Safety checks** — `/run` and `/runsh` have an allowlist; dangerous commands are blocked

## What's next

- [ ] **Xcode / iOS builds** — install Xcode to enable `/build ios` and `/demo ios`
- [ ] **Deploy to physical iPhone** — `/deploy ios` is wired up, needs Xcode + provisioning profile
- [ ] **ws-scrcpy for interactive Android** — mirror the emulator in your phone's browser via `/mirror start`
- [ ] **Showcase gallery** — `/showcase gallery` to browse all published demos
- [ ] **Multi-user support** — let others build apps too (currently owner-only for builds)

## License

MIT
