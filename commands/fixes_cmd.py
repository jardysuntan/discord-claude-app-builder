"""commands/fixes_cmd.py — /fixes show|clear + fix logging."""

from pathlib import Path
from datetime import datetime


def _fixes_path(ws_path):
    return Path(ws_path) / ".fixes.md"


def show(ws_path, ws_key):
    p = _fixes_path(ws_path)
    if not p.exists():
        return f"📭 No fixes logged for **{ws_key}** yet."
    text = p.read_text().strip()
    if len(text) > 1800:
        text = text[-1800:]
    return f"🔧 **{ws_key}/.fixes.md**\n```markdown\n{text}\n```"


def clear(ws_path, ws_key):
    p = _fixes_path(ws_path)
    if p.exists():
        p.unlink()
        return f"🗑️ Cleared fix log for **{ws_key}**."
    return f"📭 No fix log to clear in **{ws_key}**."


def log_fix(ws_path, platform, error, fix_summary):
    """Append a fix entry to .fixes.md."""
    p = _fixes_path(ws_path)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"### [{ts}] {platform}\n"
        f"**Error:** `{error.strip()}`\n"
        f"**Fix:** {fix_summary.strip()}\n\n"
    )
    with open(p, "a") as f:
        f.write(entry)


def get_recent_fixes(ws_path, max_chars=1000):
    """Return recent fixes content for context injection, or empty string."""
    p = _fixes_path(ws_path)
    if not p.exists():
        return ""
    text = p.read_text().strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def handle_fixes(sub, ws_path, ws_key):
    match sub:
        case "show" | None:
            return show(ws_path, ws_key)
        case "clear":
            return clear(ws_path, ws_key)
        case _:
            return "`/fixes` · `/fixes show` · `/fixes clear`"
