# discord-claude-app-builder

A Discord bot that turns chat messages into cross-platform apps. Describe what you want, and it builds it — Android, iOS, and Web — all from your phone.

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

## Quick Start

DM the bot:

```
/buildapp a pomodoro timer with task categories
```

Wait a few minutes. You'll get a screenshot and a web link to try it immediately.

Then iterate:

```
@pomodoro add a dark mode toggle
@pomodoro make the timer bigger with a circular progress bar
```

See a bug? Paste a screenshot — the bot can see images and fix what it sees.

Run `/demo` to preview, `/save` to checkpoint, `/testflight` or `/playstore` to publish.

That's it. No code, no setup, no installs.

## Commands

| Command | What it does |
|---------|-------------|
| `/buildapp <description>` | Build a full app from a description |
| `@appname <request>` | Change your app with natural language |
| `/demo` | Build and preview (screenshot + web link) |
| `/save` | Save your progress (undo with `/save list`) |
| `/testflight` | Publish to iOS TestFlight |
| `/playstore` | Publish to Google Play |
| `/ls` | List and switch apps |
| `/help` | Full command reference |

**More:** [`/rename`](#all-commands) [`/spend`](#all-commands) [`/run`](#all-commands) [`/status`](#all-commands) [`/diff`](#all-commands) [`/commit`](#all-commands) [`/pr`](#all-commands) — see [All Commands](#all-commands)

## Tips

- **Be specific.** "A workout tracker with sets/reps logging and a rest timer" beats "a fitness app."
- **Iterate small.** Build the core first, then add features one at a time.
- **Share screenshots.** Paste an image of a bug or a design mockup — the bot reads images.
- **Save often.** `/save` frequently so you can roll back with `/save list`.

---

*Everything below is for self-hosting or contributing to the bot.*

## Setup

<details>
<summary><b>Prerequisites</b></summary>

- macOS (needed for iOS builds; Android + Web work on Linux)
- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Android SDK with an AVD configured
- Xcode (optional, for iOS)
- A Discord bot token ([create one](https://discord.com/developers/applications))

</details>

```bash
git clone https://github.com/jardysuntan/discord-claude-app-builder.git
cd discord-claude-app-builder
pip install -r requirements.txt
cp .env.example .env   # edit with your values
```

**Minimum `.env`:**

```bash
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_ALLOWED_USER_ID=your-discord-user-id
BASE_PROJECTS_DIR=~/Projects
CLAUDE_BIN=claude
```

**Run:**

```bash
pm2 start ecosystem.config.cjs   # recommended — auto-restarts
# or: python3 bot.py
```

<details>
<summary><b>Platform-specific setup (iOS, TestFlight, Tailscale, Supabase)</b></summary>

**iOS:**
```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
```

**TestFlight** (requires Apple Developer Program, $99/yr):
```bash
export APPLE_TEAM_ID=your-team-id
export ASC_KEY_ID=your-key-id
export ASC_ISSUER_ID=your-issuer-id
# Place .p8 at ~/.private_keys/AuthKey_<KEY_ID>.p8
```

**Tailscale** (optional, for remote access):
```bash
export TAILSCALE_HOSTNAME=100.x.x.x
```

**Supabase** (optional, auto-provisions databases):
```bash
SUPABASE_PROJECT_REF=your-project-ref
SUPABASE_MANAGEMENT_KEY=your-management-key
SUPABASE_ANON_KEY=your-anon-key
```

</details>

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
                             Screenshots, web server, device install
```

**Key design:**
- Claude sessions persist per workspace — context carries over between prompts
- Auto-fix loop — build errors get fed back to Claude (up to 8 retries)
- Crash detection — iOS/Android demos detect crash-on-launch and auto-fix
- Fix memory — every error+fix is logged and injected into future fix prompts
- Image input — Discord image attachments are saved to the workspace and read by Claude
- Web screenshots — Playwright captures a preview after every web build

<details>
<summary><b>Project structure</b></summary>

```
bot.py                  # Entry point — Discord client, message routing
parser.py               # Message grammar — slash commands + @workspace prompts
config.py               # Environment variables
platforms.py            # Build/install/demo for Android, iOS, Web
claude_runner.py        # Claude Code CLI with session continuity + progress streaming
agent_loop.py           # Auto-fix loop: build → error → Claude fix → rebuild
bot_context.py          # Shared context + message splitting
workspaces.py           # Workspace registry (JSON-backed)
supabase_client.py      # Supabase Management API client
handlers/
  prompt_handler.py     # Core prompt flow (images, Claude, auto-build, preview)
  build_commands.py     # /buildapp, /demo, /platform
  save_git_commands.py  # /save, git commands
  workspace_commands.py # /ls, /use, /rename, /help
  publish_commands.py   # /testflight, /playstore
  system_commands.py    # /spend, /setup, /health, /admin
helpers/
  demo_runner.py        # Platform demo orchestration
  web_screenshot.py     # Playwright headless screenshots
  pro_tips.py           # Pro tips embed + dismiss
  ui_helpers.py         # Help text, workspace footer
commands/
  create.py             # Project scaffolding + CLAUDE.md template
  buildapp.py           # /buildapp full pipeline
  git_cmd.py            # Git operations
  testflight.py         # iOS TestFlight upload
  playstore.py          # Google Play upload
  queue.py              # Batch task queue
templates/
  kmp/KMPTemplate/      # KMP project template (copied per new app)
```

</details>

## All Commands

| Command | What it does |
|---------|-------------|
| `/buildapp <description>` | Full pipeline: scaffold → build → demo |
| `@workspace <prompt>` | Send a prompt to Claude in that project |
| `/demo [web\|android\|ios]` | Build + preview (default: web) |
| `/save` | Save with auto-generated description |
| `/save <message>` | Save with custom description |
| `/save list` | Browse saves, restore any version |
| `/save undo` / `redo` | Quick undo/redo |
| `/save github` | Push to GitHub |
| `/testflight` | Upload to iOS TestFlight |
| `/playstore` | Upload to Google Play |
| `/ls` | List and switch workspaces |
| `/use <name>` | Switch workspace |
| `/rename <new name>` | Rename current workspace |
| `/remove <name>` | Delete a workspace |
| `/spend` | Daily budget and usage |
| `/platform [web\|ios\|android]` | Set default demo platform |
| `/status` `/diff` `/commit` `/log` `/pr` | Git workflow |
| `/run <cmd>` | Run shell command in workspace |
| `/collaborate <ws> <name> <email>` | Invite collaborator |
| `/maintenance [msg\|off]` | Toggle maintenance mode |
| `/help` | Full command reference |

## License

MIT
