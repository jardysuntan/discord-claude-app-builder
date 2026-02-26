"""
parser.py — Message grammar for KMP cross-platform bot.

New/updated commands:
    /buildapp <desc>              → build full app (Android + iOS + Web)
    /build android|ios|web        → build specific platform
    /demo android|ios|web         → demo specific platform
    /vid android                  → video from Android emulator
    /fix [instructions]           → auto-fix build errors
    /widget <description>         → add iOS home screen widget
    /create <AppName>             → scaffold KMP project
    /tryapp <ws> [platform]       → let anyone try the app
    /showcase <ws>                → post demo for everyone
"""

from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class WorkspacePrompt:
    workspace: str
    prompt: str


@dataclass
class Command:
    name: str
    workspace: Optional[str] = None
    raw_cmd: Optional[str] = None
    app_name: Optional[str] = None
    platform: Optional[str] = None  # "android", "ios", "web", or None for all
    sub: Optional[str] = None
    arg: Optional[str] = None


@dataclass
class FallbackPrompt:
    prompt: str


ParseResult = WorkspacePrompt | Command | FallbackPrompt

PLATFORMS = {"android", "ios", "web"}


def _parse_platform(text: str) -> tuple[Optional[str], str]:
    """Extract a platform keyword from the start of text, return (platform, rest)."""
    parts = text.split(None, 1)
    if parts and parts[0].lower() in PLATFORMS:
        return parts[0].lower(), parts[1] if len(parts) > 1 else ""
    return None, text


def parse(text: str) -> ParseResult:
    text = text.strip()

    # @workspace prompt
    m = re.match(r"^@(\S+)\s+(.+)", text, re.DOTALL)
    if m:
        return WorkspacePrompt(workspace=m.group(1).lower(), prompt=m.group(2).strip())

    # Slash commands
    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        match cmd:
            case "/help":
                return Command(name="help")
            case "/ls" | "/workspaces":
                return Command(name="ls")
            case "/use":
                return Command(name="use", workspace=rest.lower() or None)
            case "/where":
                return Command(name="where")

            # ── Build & run ──────────────────────────────────────────
            case "/buildapp" | "/build-app":
                return Command(name="buildapp", raw_cmd=rest or None)

            case "/build":
                # "/build app <desc>" is an alias for "/buildapp <desc>"
                if rest and rest.lower().startswith("app"):
                    buildapp_rest = rest[3:].strip()
                    return Command(name="buildapp", raw_cmd=buildapp_rest or None)
                platform, remainder = _parse_platform(rest)
                return Command(name="build", platform=platform or "all")

            case "/create":
                return Command(name="create", app_name=rest or None)

            case "/deleteapp" | "/remove":
                return Command(name="deleteapp", workspace=rest.lower() if rest else None)

            case "/rename":
                rename_parts = rest.split(None, 1) if rest else []
                return Command(name="rename", raw_cmd=rest or None,
                               workspace=rename_parts[0].lower() if rename_parts else None,
                               arg=rename_parts[1].lower() if len(rename_parts) > 1 else None)

            case "/demo":
                platform, _ = _parse_platform(rest)
                return Command(name="demo", platform=platform)

            case "/deploy":
                platform, _ = _parse_platform(rest)
                return Command(name="deploy", platform=platform or "ios")

            case "/testflight":
                return Command(name="testflight")

            case "/vid" | "/viddemo":
                return Command(name="vid", platform="android")

            case "/fix":
                return Command(name="fix", raw_cmd=rest or None)

            case "/widget":
                return Command(name="widget", raw_cmd=rest or None)

            # ── Terminal ─────────────────────────────────────────────
            case "/run":
                return Command(name="run", raw_cmd=rest or None)
            case "/runsh":
                return Command(name="runsh", raw_cmd=rest or None)

            # ── Git & GitHub ─────────────────────────────────────────
            case "/status":
                return Command(name="gitstatus")
            case "/diff":
                return Command(name="diff", sub=rest.lower() if rest else None)
            case "/commit":
                return Command(name="commit", raw_cmd=rest or None)
            case "/undo":
                return Command(name="undo")
            case "/log":
                return Command(name="gitlog", raw_cmd=rest or None)
            case "/branch":
                return Command(name="branch", raw_cmd=rest or None)
            case "/stash":
                is_pop = rest.lower().startswith("pop") if rest else False
                return Command(name="stash", sub="pop" if is_pop else "push")
            case "/pr":
                return Command(name="pr", raw_cmd=rest or None)
            case "/repo":
                repo_parts = rest.split(None, 1) if rest else []
                sub = repo_parts[0].lower() if repo_parts else None
                arg = repo_parts[1] if len(repo_parts) > 1 else None
                return Command(name="repo", sub=sub, arg=arg)

            # ── Mirror & showcase ────────────────────────────────────
            case "/mirror":
                return Command(name="mirror", sub=rest.split()[0].lower() if rest else "start")
            case "/showcase":
                if rest.lower() == "gallery":
                    return Command(name="gallery")
                return Command(name="showcase", workspace=rest.lower() if rest else None)
            case "/tryapp":
                parts_try = rest.split()
                ws = parts_try[0].lower() if parts_try else None
                plat = parts_try[1].lower() if len(parts_try) > 1 and parts_try[1].lower() in PLATFORMS else None
                return Command(name="tryapp", workspace=ws, platform=plat)
            case "/done":
                return Command(name="done")

            # ── Queue & spend ────────────────────────────────────────
            case "/queue":
                return Command(name="queue", raw_cmd=rest or None)
            case "/spend":
                return Command(name="spend")

            # ── Memory & system ──────────────────────────────────────
            case "/memory":
                mem_parts = rest.split(None, 1)
                sub = mem_parts[0].lower() if mem_parts else None
                arg = mem_parts[1] if len(mem_parts) > 1 else None
                return Command(name="memory", sub=sub, arg=arg)
            case "/fixes":
                return Command(name="fixes", sub=rest.lower().strip() if rest else None)
            case "/setup":
                return Command(name="setup")
            case "/health":
                return Command(name="health")
            case "/reload":
                return Command(name="reload")
            case "/patch-bot":
                return Command(name="patch-bot", raw_cmd=rest or None)
            case "/bot-todo":
                return Command(name="bot-todo", raw_cmd=rest or None)
            case "/dashboard":
                return Command(name="dashboard", sub=rest.lower().strip() if rest else None)
            case "/newsession":
                return Command(name="newsession")
            case "/maintenance":
                return Command(name="maintenance", raw_cmd=rest or None)
            case "/announce":
                return Command(name="announce", raw_cmd=rest or None)
            case _:
                return Command(name="unknown", raw_cmd=text)

    return FallbackPrompt(prompt=text)
