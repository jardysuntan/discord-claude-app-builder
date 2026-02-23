"""commands/memory_cmd.py — /memory show|pin|reset."""

from pathlib import Path
from datetime import datetime

def _md_path(ws_path):
    return Path(ws_path) / "CLAUDE.md"

def show(ws_path, ws_key):
    p = _md_path(ws_path)
    if not p.exists():
        return f"📭 No CLAUDE.md in **{ws_key}**. Use `/memory pin <note>`."
    text = p.read_text().strip()
    if len(text) > 1800:
        text = text[:1800] + "\n…"
    return f"📝 **{ws_key}/CLAUDE.md**\n```markdown\n{text}\n```"

def pin(ws_path, note):
    if not note:
        return "Usage: `/memory pin <note>`"
    p = _md_path(ws_path)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not p.exists():
        p.write_text(f"# Project Memory\n\n- [{ts}] {note}\n")
    else:
        with open(p, "a") as f:
            f.write(f"- [{ts}] {note}\n")
    return f"📌 Pinned:\n> {note}"

def reset(ws_path, ws_key):
    p = _md_path(ws_path)
    if p.exists():
        p.unlink()
        return f"🗑️ Cleared CLAUDE.md for **{ws_key}**."
    return f"📭 Nothing to clear in **{ws_key}**."

def handle_memory(sub, arg, ws_path, ws_key):
    match sub:
        case "show":  return show(ws_path, ws_key)
        case "pin":   return pin(ws_path, arg or "")
        case "reset": return reset(ws_path, ws_key)
        case _:       return "`/memory show` · `/memory pin <note>` · `/memory reset`"
