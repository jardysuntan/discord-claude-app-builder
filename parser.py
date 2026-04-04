"""
parser.py — Message grammar for KMP cross-platform bot.
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

PLATFORMS = {"android", "ios", "web", "all"}


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

            # ── Build & run ──────────────────────────────────────────
            case "/buildapp" | "/build-app":
                return Command(name="buildapp", raw_cmd=rest or None)

            case "/planapp" | "/plan-app" | "/plan":
                return Command(name="planapp", raw_cmd=rest or None)

            case "/build":
                # "/build app <desc>" is an alias for "/buildapp <desc>"
                if rest and rest.lower().startswith("app"):
                    buildapp_rest = rest[3:].strip()
                    return Command(name="buildapp", raw_cmd=buildapp_rest or None)
                platform, remainder = _parse_platform(rest)
                return Command(name="build", platform=platform or "all")

            case "/deleteapp" | "/remove":
                return Command(name="deleteapp", workspace=rest.lower() if rest else None)

            case "/rename":
                return Command(name="rename", raw_cmd=rest)

            case "/platform":
                return Command(name="platform", platform=rest.strip().lower() if rest else None)

            case "/demo":
                platform, _ = _parse_platform(rest)
                return Command(name="demo", platform=platform)

            case "/appraise" | "/appcheck" | "/review":
                return Command(name="appraise")

            case "/integrate" | "/integration" | "/addintegration":
                return Command(name="integrate", raw_cmd=rest or None)

            case "/testflight":
                return Command(name="testflight")

            case "/playstore":
                return Command(name="playstore")

            case "/appname":
                return Command(name="appname", raw_cmd=rest)

            # ── Terminal ─────────────────────────────────────────────
            case "/run":
                return Command(name="run", raw_cmd=rest or None)
            case "/runsh":
                return Command(name="runsh", raw_cmd=rest or None)

            # ── Save (game-save-style versioning) ─────────────────────
            case "/save":
                sub = rest.split()[0].lower() if rest else None
                if sub in ("list", "undo", "redo", "github"):
                    return Command(name="save", sub=sub)
                # anything else is a custom save message: /save fixed the colors
                return Command(name="save", sub=None, raw_cmd=rest or None)

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

            # ── Data ──────────────────────────────────────────────────
            case "/syncdoc" | "/sync-doc":
                return Command(name="syncdoc", raw_cmd=rest or None)

            case "/data":
                data_parts = rest.split(None, 1) if rest else []
                sub = data_parts[0].lower() if data_parts else None
                arg = data_parts[1] if len(data_parts) > 1 else None
                return Command(name="data", sub=sub, arg=arg)

            # ── Spend ─────────────────────────────────────────────────
            case "/spend":
                return Command(name="spend")

            # ── User management ───────────────────────────────────────
            case "/allow":
                return Command(name="allow", raw_cmd=rest or None)
            case "/disallow":
                return Command(name="disallow", raw_cmd=rest or None)
            case "/setcap":
                return Command(name="setcap", raw_cmd=rest or None)
            case "/users":
                return Command(name="users")
            case "/invite":
                return Command(name="invite", raw_cmd=rest or None)
            case "/collaborate":
                return Command(name="collaborate", raw_cmd=rest or None)
            case "/admin":
                return Command(name="admin")

            # ── Dashboard ─────────────────────────────────────────────
            case "/history":
                return Command(name="dashboard", raw_cmd=rest or None)

            # ── Analytics ─────────────────────────────────────────────
            case "/analytics":
                return Command(name="analytics", workspace=rest.lower() if rest else None)

            # ── System ────────────────────────────────────────────────
            case "/setup":
                return Command(name="setup")
            case "/health":
                return Command(name="health")
            case "/reload":
                return Command(name="reload")
            case "/bot-todo":
                return Command(name="bot-todo", raw_cmd=rest or None)
            case "/newsession":
                return Command(name="newsession")
            case "/maintenance":
                return Command(name="maintenance", raw_cmd=rest or None)
            case "/announce":
                return Command(name="announce", raw_cmd=rest or None)
            case "/smoketest" | "/smoke-test":
                return Command(name="smoketest")
            case "/testnewuser":
                return Command(name="testnewuser")
            case "/testpublish":
                return Command(name="testpublish")
            case _:
                return Command(name="unknown", raw_cmd=text)

    return FallbackPrompt(prompt=text)
