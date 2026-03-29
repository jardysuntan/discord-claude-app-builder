import os
import json
import asyncio
import datetime
import discord
import shlex
import re
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

# ===== Config =====
TOKEN = os.environ["DISCORD_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ.get("DISCORD_ALLOWED_USER_ID", "0"))

WORKSPACES_PATH = os.environ.get("WORKSPACES_PATH", "./workspaces.json")
DEFAULT_WORKSPACE = os.environ.get("DEFAULT_WORKSPACE", "")

BASE_PROJECTS_DIR = Path(os.environ.get("BASE_PROJECTS_DIR", str(Path.home() / "Projects"))).expanduser()
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", str(Path.home() / "bots/discord-claude-bridge/templates"))).expanduser()

ANDROID_PACKAGE_PREFIX = os.environ.get("ANDROID_PACKAGE_PREFIX", "com.jaredtan").strip().rstrip(".")
TEMPLATE_OLD_PKG = os.environ.get("TEMPLATE_OLD_PKG", "com.jaredtan.androidtemplate").strip()

ADB_BIN = os.environ.get("ADB_BIN", "adb")
EMULATOR_BIN = os.environ.get("EMULATOR_BIN", "emulator")
ANDROID_AVD = os.environ.get("ANDROID_AVD", "")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
AGENT_MODE = os.environ.get("AGENT_MODE", "0") == "1"

# ===== Agent constants =====
MAX_AGENT_CONTEXT = 10          # keep last N instruction+result pairs (20 total entries)
MAX_FILE_WRITE_BYTES = 1_572_864  # 1.5 MB hard cap on write_file / append_to_file
AGENT_BACKUP_DIR_NAME = ".agent_backups"

AGENT_RUN_ALLOWLIST = {
    "./gradlew test",
    "./gradlew :app:testDebugUnitTest",
    "./gradlew assembleDebug",
    "./gradlew :app:assembleDebug",
    "./gradlew installDebug",
    "./gradlew :app:installDebug",
    "./gradlew lint",
    "./gradlew :app:lint",
    "git status",
    "git diff",
}

AGENT_SYSTEM_PROMPT = """\
You are a precise code-editing agent. The user will give you an instruction and optional conversation history.

You MUST output ONLY a single, valid JSON object — no markdown, no explanation, no code fences.
The JSON must conform exactly to this schema:

{
  "summary": "<one-line human-readable summary of what you will do>",
  "steps": [
    {"type": "write_file", "path": "relative/path/to/file.ext", "content": "<full file content>"},
    {"type": "replace_in_file", "path": "relative/path", "search": "<exact text to find>", "replace": "<replacement text>"},
    {"type": "append_to_file", "path": "relative/path", "content": "<text to append>"},
    {"type": "mkdir", "path": "relative/dir/path"},
    {"type": "delete_path", "path": "relative/path"},
    {"type": "run", "cmd": "<exact allowlisted command>"}
  ],
  "notes": "<optional: extra context or caveats for the user>",
  "ask_user": "<optional: if you need clarification before proceeding, put your question here and use an empty steps list>"
}

Rules:
- Paths must be relative to the workspace root. Never use absolute paths or leading slashes.
- Never traverse outside the workspace with ".." or similar.
- Never touch .git/ or .gradle/ or .agent_backups/ directories.
- For "run", only these exact commands are allowed:
    ./gradlew test
    ./gradlew :app:testDebugUnitTest
    ./gradlew assembleDebug
    ./gradlew :app:assembleDebug
    ./gradlew installDebug
    ./gradlew :app:installDebug
    ./gradlew lint
    ./gradlew :app:lint
    git status
    git diff
- Use replace_in_file for small targeted edits; use write_file to create new files or fully rewrite one.
- If you have nothing to do, output an empty steps list with a summary explaining why.
- If your output is not parseable JSON, the system will reject it.
"""

# ===== Discord client =====
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# per-user default workspace (DM-only)
user_default_ws = {}

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bsudo\b",
    r"\bshutdown\b|\breboot\b",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*;\s*\}\s*;\s*:",  # fork bomb
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r"\bchown\b|\bchmod\b\s+777\b",
    r"\bcurl\b.+\|\s*(bash|sh)\b",
    r"\bwget\b.+\|\s*(bash|sh)\b",
]

# ===== Agent state (in-memory) =====
_agent_state: dict = {}
# user_id -> {
#   "enabled": bool,
#   "busy": bool,
#   "context": [{"role": "user"|"assistant", "content": str}, ...],
#   "last_run": Optional[datetime.datetime],
#   "last_cmds": [(cmd_str, rc_or_None), ...],
#   "ws_backups": {ws_key: [backup_dir_str, ...]},
# }

MAX_AGENT_MEMORY = 15          # keep last N entries per (user_id, workspace) key
AGENT_MEMORY_FILE = ".agent_memory.json"

# (user_id, ws_key) -> [{"user": str, "summary": str}, ...]
_agent_memory: dict = {}
# tracks which (user_id, ws_key) pairs have been loaded from disk
_memory_loaded: set = set()


# ===== Helpers =====
def load_workspaces() -> dict:
    if not Path(WORKSPACES_PATH).exists():
        return {}
    with open(WORKSPACES_PATH, "r") as f:
        return json.load(f)


def save_workspaces(workspaces: dict):
    with open(WORKSPACES_PATH, "w") as f:
        json.dump(workspaces, f, indent=2)


def is_under_base(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def _mem_key(user_id: int, ws_key: str) -> tuple:
    return (user_id, ws_key)


def load_agent_memory(user_id: int, ws_key: str, workspace_root: Path) -> None:
    """Load memory from <workspace>/.agent_memory.json on first access."""
    key = _mem_key(user_id, ws_key)
    if key in _memory_loaded:
        return
    _memory_loaded.add(key)
    mem_file = workspace_root / AGENT_MEMORY_FILE
    if not mem_file.exists():
        return
    try:
        data = json.loads(mem_file.read_text())
        user_key = str(user_id)
        entries = data.get(user_key, [])
        if isinstance(entries, list):
            _agent_memory[key] = entries[-MAX_AGENT_MEMORY:]
    except Exception:
        pass


def save_agent_memory(user_id: int, ws_key: str, workspace_root: Path) -> None:
    """Persist current memory list to <workspace>/.agent_memory.json."""
    key = _mem_key(user_id, ws_key)
    mem_file = workspace_root / AGENT_MEMORY_FILE
    try:
        data: dict = {}
        if mem_file.exists():
            try:
                data = json.loads(mem_file.read_text())
            except Exception:
                data = {}
        data[str(user_id)] = _agent_memory.get(key, [])
        mem_file.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def append_agent_memory(user_id: int, ws_key: str, user_msg: str, summary: str) -> None:
    """Append one entry and trim to MAX_AGENT_MEMORY."""
    key = _mem_key(user_id, ws_key)
    entries = _agent_memory.setdefault(key, [])
    entries.append({"user": user_msg[:300], "summary": summary[:300]})
    if len(entries) > MAX_AGENT_MEMORY:
        _agent_memory[key] = entries[-MAX_AGENT_MEMORY:]


def looks_dangerous(cmd: str) -> bool:
    c = cmd.strip()
    return any(re.search(p, c) for p in DANGEROUS_PATTERNS)


def chunk_for_discord(s: str, size: int = 1900):
    s = (s or "").strip() or "(empty)"
    for i in range(0, len(s), size):
        yield s[i:i + size]


async def run_proc(args, cwd: Path, timeout_s: int = 180):
    """Run a subprocess safely (no shell). args is a list."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, b"", b"Timed out."
    return proc.returncode, stdout, stderr


async def run_shell_command(cmd: str, cwd: Path):
    """
    /run command.
    We parse with shlex and execute without shell.
    Blocks pipes/redirection/operators by design.
    """
    if looks_dangerous(cmd):
        return "❌ Blocked: command looks dangerous."

    if any(op in cmd for op in ["|", "&&", "||", ">", "<", ";"]):
        return "❌ Blocked: pipes/redirection/operators are disabled in /run for safety. Run a single command."

    try:
        args = shlex.split(cmd)
    except ValueError as e:
        return f"❌ Could not parse command: {e}"

    if not args:
        return "❌ Empty command."

    rc, out, err = await run_proc(args, cwd=cwd, timeout_s=900)
    out_s = out.decode(errors="ignore")
    err_s = err.decode(errors="ignore")

    if rc != 0:
        return f"⚠️ Exit {rc}\nSTDOUT:\n{out_s[:1500]}\n\nSTDERR:\n{err_s[:1500]}"
    return f"✅ Exit {rc}\n{out_s[:1900]}"


async def run_claude(prompt: str, cwd: Path) -> str:
    rc, out, err = await run_proc([CLAUDE_BIN, "-p", prompt], cwd=cwd, timeout_s=900)
    if rc != 0:
        err_s = err.decode(errors="ignore") or out.decode(errors="ignore")
        return f"⚠️ Claude CLI error (exit {rc}):\n{err_s[:1800]}"
    return out.decode(errors="ignore")


# ===== Agent helpers =====

def get_agent_state(user_id: int) -> dict:
    """Return the agent state dict for user_id, creating it if missing."""
    if user_id not in _agent_state:
        _agent_state[user_id] = {
            "enabled": False,
            "busy": False,
            "context": [],
            "last_run": None,
            "last_cmds": [],
            "ws_backups": {},
        }
    return _agent_state[user_id]


def validate_agent_path(rel_path_str: str, workspace_root: Path) -> Optional[Path]:
    """
    Validate a relative path from the agent JSON plan.
    Returns the resolved absolute Path if safe, or None if rejected.
    Rejects: absolute paths, ".." components, .git/.gradle/.agent_backups roots,
    and anything resolving outside workspace_root.
    """
    s = rel_path_str.strip()
    if not s:
        return None
    if s.startswith("/"):
        return None
    parts = Path(s).parts
    if not parts:
        return None
    if ".." in parts:
        return None
    if parts[0] in (".git", ".gradle", ".agent_backups"):
        return None
    resolved = (workspace_root / s).resolve()
    try:
        resolved.relative_to(workspace_root.resolve())
    except ValueError:
        return None
    return resolved


def create_backup(
    user_id: int,
    ws_key: str,
    workspace_root: Path,
    paths_to_backup: list,
) -> Optional[Path]:
    """
    Copy files in paths_to_backup into a timestamped backup directory.
    Files that don't yet exist are recorded as "new" so /undo can delete them.
    Registers the backup dir in _agent_state[user_id]["ws_backups"][ws_key].
    Returns the backup dir Path on success, None on failure.
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = workspace_root / AGENT_BACKUP_DIR_NAME / f"{stamp}_{user_id}"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None

    meta_files = []
    for abs_path in paths_to_backup:
        try:
            rel = abs_path.resolve().relative_to(workspace_root.resolve())
        except ValueError:
            continue
        if abs_path.exists():
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if abs_path.is_file():
                shutil.copy2(str(abs_path), str(dest))
            elif abs_path.is_dir():
                shutil.copytree(str(abs_path), str(dest), dirs_exist_ok=True)
            meta_files.append({"rel": str(rel), "status": "exists"})
        else:
            meta_files.append({"rel": str(rel), "status": "new"})

    meta = {
        "user_id": user_id,
        "ws_key": ws_key,
        "created": datetime.datetime.now().isoformat(),
        "files": meta_files,
    }
    try:
        (backup_dir / "backup.json").write_text(json.dumps(meta, indent=2))
    except Exception:
        return None

    state = get_agent_state(user_id)
    ws_list = state["ws_backups"].setdefault(ws_key, [])
    ws_list.append(str(backup_dir))
    return backup_dir


def restore_backup(backup_dir: Path, workspace_root: Path) -> tuple:
    """
    Restore files from a backup directory.
    "exists" entries are copied back; "new" entries (created by agent) are deleted.
    Returns (ok: bool, message: str).
    """
    meta_path = backup_dir / "backup.json"
    if not meta_path.exists():
        return False, "backup.json not found in backup directory."
    try:
        meta = json.loads(meta_path.read_text())
    except Exception as e:
        return False, f"Could not parse backup.json: {e}"

    errors = []
    for entry in meta.get("files", []):
        rel = entry["rel"]
        status = entry["status"]
        target = workspace_root / rel
        if status == "exists":
            src = backup_dir / rel
            if not src.exists():
                errors.append(f"Missing backup source: {rel}")
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if src.is_file():
                    shutil.copy2(str(src), str(target))
                elif src.is_dir():
                    if target.exists():
                        shutil.rmtree(str(target))
                    shutil.copytree(str(src), str(target))
            except Exception as e:
                errors.append(f"Restore failed for {rel}: {e}")
        elif status == "new":
            if target.exists():
                try:
                    if target.is_file() or target.is_symlink():
                        target.unlink()
                    elif target.is_dir():
                        shutil.rmtree(str(target))
                except Exception as e:
                    errors.append(f"Delete failed for {rel}: {e}")

    if errors:
        return False, "Restore completed with errors:\n" + "\n".join(errors)
    return True, "Restore complete."


def collect_paths_for_plan(plan_steps: list, workspace_root: Path) -> list:
    """
    Return a list of absolute Paths affected by file-touching steps (for backup).
    Includes paths that don't yet exist (will be marked "new" in backup).
    """
    paths = []
    for step in plan_steps:
        if step.get("type") in ("write_file", "replace_in_file", "append_to_file", "delete_path"):
            rel = step.get("path", "")
            if rel:
                abs_p = validate_agent_path(rel, workspace_root)
                if abs_p is not None:
                    paths.append(abs_p)
    return paths


def validate_plan(plan: dict, workspace_root: Path) -> tuple:
    """
    Validate the parsed JSON plan from Claude.
    Returns (ok: bool, error_message: str, validated_steps: list).
    Each validated step has an "abs_path" key added where relevant.
    """
    if not isinstance(plan, dict):
        return False, "Plan is not a JSON object.", []
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        return False, "Plan 'steps' is not a list.", []

    validated = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, f"Step {i} is not an object.", []
        stype = step.get("type", "")
        label = f"Step {i} ({stype})"

        if stype in ("write_file", "replace_in_file", "append_to_file", "mkdir", "delete_path"):
            rel = step.get("path", "")
            abs_p = validate_agent_path(rel, workspace_root)
            if abs_p is None:
                return False, f"{label}: invalid or unsafe path '{rel}'.", []
            if stype == "delete_path":
                parts = Path(rel).parts
                if ".git" in parts or ".gradle" in parts or ".agent_backups" in parts:
                    return False, f"{label}: cannot delete .git, .gradle, or .agent_backups.", []
            step = dict(step)
            step["abs_path"] = abs_p
            if stype in ("write_file", "append_to_file"):
                content = step.get("content", "")
                if len(content.encode("utf-8")) > MAX_FILE_WRITE_BYTES:
                    return False, f"{label}: content exceeds {MAX_FILE_WRITE_BYTES} bytes.", []

        elif stype == "run":
            cmd = step.get("cmd", "").strip()
            if cmd not in AGENT_RUN_ALLOWLIST:
                return False, f"{label}: command not in allowlist: '{cmd}'. Use /run manually if needed.", []
            step = dict(step)

        else:
            return False, f"{label}: unknown step type '{stype}'.", []

        validated.append(step)

    return True, "", validated


async def apply_agent_plan(steps: list, workspace_root: Path) -> list:
    """
    Execute validated plan steps. Returns list of result strings (one per step).
    Steps must already be validated (have abs_path where relevant).
    """
    results = []
    for step in steps:
        stype = step["type"]
        try:
            if stype == "write_file":
                abs_p = step["abs_path"]
                abs_p.parent.mkdir(parents=True, exist_ok=True)
                abs_p.write_text(step.get("content", ""), encoding="utf-8")
                results.append(f"write_file OK: {step['path']}")

            elif stype == "replace_in_file":
                abs_p = step["abs_path"]
                if not abs_p.exists():
                    results.append(f"replace_in_file SKIP (not found): {step['path']}")
                    continue
                text = abs_p.read_text(encoding="utf-8", errors="replace")
                search = step.get("search", "")
                replace = step.get("replace", "")
                if search not in text:
                    results.append(f"replace_in_file WARN (search string not found): {step['path']}")
                    continue
                abs_p.write_text(text.replace(search, replace, 1), encoding="utf-8")
                results.append(f"replace_in_file OK: {step['path']}")

            elif stype == "append_to_file":
                abs_p = step["abs_path"]
                abs_p.parent.mkdir(parents=True, exist_ok=True)
                with abs_p.open("a", encoding="utf-8") as f:
                    f.write(step.get("content", ""))
                results.append(f"append_to_file OK: {step['path']}")

            elif stype == "mkdir":
                abs_p = step["abs_path"]
                abs_p.mkdir(parents=True, exist_ok=True)
                results.append(f"mkdir OK: {step['path']}")

            elif stype == "delete_path":
                abs_p = step["abs_path"]
                if not abs_p.exists():
                    results.append(f"delete_path SKIP (not found): {step['path']}")
                    continue
                if abs_p.is_file() or abs_p.is_symlink():
                    abs_p.unlink()
                elif abs_p.is_dir():
                    shutil.rmtree(str(abs_p))
                results.append(f"delete_path OK: {step['path']}")

            elif stype == "run":
                cmd = step["cmd"].strip()
                rc, out, err = await run_proc(shlex.split(cmd), cwd=workspace_root, timeout_s=600)
                out_s = out.decode(errors="ignore")[:800]
                err_s = err.decode(errors="ignore")[:400]
                line = f"run `{cmd}` → exit {rc}\n{out_s}"
                if err_s:
                    line += f"\nSTDERR: {err_s}"
                results.append(line)

        except Exception as e:
            results.append(f"{stype} ERROR on {step.get('path', step.get('cmd', '?'))}: {e}")

    return results


def build_agent_prompt(instruction: str, context: list, memory: Optional[list] = None) -> str:
    """Build the full prompt for Claude, with conversation history and persistent memory."""
    parts = [AGENT_SYSTEM_PROMPT, ""]
    if memory:
        parts.append("=== Persistent memory (past sessions, oldest first) ===")
        for entry in memory:
            parts.append(f"[USER]: {entry['user']}")
            parts.append(f"[AGENT SUMMARY]: {entry['summary']}")
        parts.append("=== End of memory ===")
        parts.append("")
    if context:
        parts.append("=== Conversation history (most recent last) ===")
        for entry in context:
            role = entry["role"].upper()
            parts.append(f"[{role}]: {entry['content']}")
        parts.append("=== End of history ===")
        parts.append("")
    parts.append(f"[USER INSTRUCTION]: {instruction}")
    return "\n".join(parts)


def trim_context(context: list) -> list:
    """Keep only the last MAX_AGENT_CONTEXT instruction+result pairs."""
    max_entries = MAX_AGENT_CONTEXT * 2
    if len(context) > max_entries:
        return context[-max_entries:]
    return context


async def run_agent(
    instruction: str,
    user_id: int,
    ws_key: str,
    workspace_root: Path,
    channel,
) -> None:
    """
    Full agent pipeline:
      1. Build prompt with history
      2. Call Claude (JSON mode)
      3. Parse + validate plan
      4. Create backup
      5. Apply plan
      6. Update context
      7. Report results
    """
    state = get_agent_state(user_id)
    load_agent_memory(user_id, ws_key, workspace_root)
    memory = _agent_memory.get(_mem_key(user_id, ws_key), [])
    prompt = build_agent_prompt(instruction, state["context"], memory)

    await channel.send("🤖 Agent thinking…")
    raw_reply = await run_claude(prompt, workspace_root)

    # Strip markdown fences if Claude wraps output despite instructions
    raw_stripped = raw_reply.strip()
    if raw_stripped.startswith("```"):
        raw_stripped = re.sub(r"^```[a-zA-Z]*\n?", "", raw_stripped)
        raw_stripped = re.sub(r"\n?```$", "", raw_stripped.strip())

    try:
        plan = json.loads(raw_stripped)
    except json.JSONDecodeError as e:
        await channel.send(
            f"❌ Agent returned non-JSON output (parse error: {e}).\n"
            f"Raw output (first 800 chars):\n```\n{raw_stripped[:800]}\n```"
        )
        state["context"].append({"role": "user", "content": instruction})
        state["context"].append({"role": "assistant", "content": f"[ERROR: non-JSON] {raw_stripped[:400]}"})
        state["context"] = trim_context(state["context"])
        return

    # Claude wants clarification — ask and stop without applying steps
    if plan.get("ask_user"):
        state["context"].append({"role": "user", "content": instruction})
        state["context"].append({"role": "assistant", "content": f"[asked user]: {plan['ask_user']}"})
        state["context"] = trim_context(state["context"])
        await channel.send(f"🤔 {plan['ask_user']}")
        return

    ok, err_msg, validated_steps = validate_plan(plan, workspace_root)
    if not ok:
        await channel.send(f"❌ Plan validation failed: {err_msg}")
        state["context"].append({"role": "user", "content": instruction})
        state["context"].append({"role": "assistant", "content": f"[PLAN INVALID: {err_msg}]"})
        state["context"] = trim_context(state["context"])
        return

    summary = plan.get("summary", "(no summary)")
    notes = plan.get("notes", "")

    # Backup files that will be affected
    paths_affected = collect_paths_for_plan(validated_steps, workspace_root)
    backup_dir = None
    if paths_affected:
        backup_dir = create_backup(user_id, ws_key, workspace_root, paths_affected)
        if backup_dir is None:
            await channel.send("⚠️ Could not create backup. Aborting for safety.")
            return

    if not validated_steps:
        state["context"].append({"role": "user", "content": instruction})
        state["context"].append({"role": "assistant", "content": f"[summary: {summary}] (no steps)"})
        state["context"] = trim_context(state["context"])
        await channel.send(f"🤖 **{summary}** — no steps to apply.")
        return

    await channel.send(f"🤖 Plan: **{summary}**\nApplying {len(validated_steps)} step(s)…")
    step_results = await apply_agent_plan(validated_steps, workspace_root)
    result_text = "\n".join(step_results)

    # Track last run commands
    run_steps = [s for s in validated_steps if s["type"] == "run"]
    state["last_cmds"] = [(s["cmd"], None) for s in run_steps]
    state["last_run"] = datetime.datetime.now()

    # Update rolling context
    assistant_ctx = f"[summary: {summary}] [steps: {len(validated_steps)}]\n{result_text}"
    if notes:
        assistant_ctx += f"\n[notes: {notes}]"
    state["context"].append({"role": "user", "content": instruction})
    state["context"].append({"role": "assistant", "content": assistant_ctx})
    state["context"] = trim_context(state["context"])

    # Persist memory entry
    append_agent_memory(user_id, ws_key, instruction, summary)
    save_agent_memory(user_id, ws_key, workspace_root)

    # Build reply
    reply_lines = [f"**{summary}**"]
    if notes:
        reply_lines.append(f"Notes: {notes}")
    if backup_dir:
        reply_lines.append(f"Backup: `{backup_dir.name}` — use `/undo` to restore")
    reply_lines.append("")
    reply_lines.append("**Step results:**")
    reply_lines.append(result_text)

    for part in chunk_for_discord("\n".join(reply_lines)):
        await channel.send(part)


# ===== Template / Android helpers =====

def parse(text: str, default_ws: str):
    t = text.strip()

    if t in ["/help", "help"]:
        return ("__CMD__", "help")

    if t.startswith("/ls"):
        return ("__CMD__", "ls")

    if t.startswith("/where"):
        return ("__CMD__", "where")

    if t.startswith("/use "):
        ws = t.split(" ", 1)[1].strip()
        return ("__CMD__", f"use:{ws}")

    if t.startswith("/run "):
        return ("__CMD__", f"run:{t.split(' ', 1)[1].strip()}")

    if t.startswith("/create "):
        return ("__CMD__", f"create:{t.split(' ', 1)[1].strip()}")

    if t.startswith("/demo"):
        return ("__CMD__", "demo")

    if t.startswith("/viddemo"):
        secs = t.split(" ", 1)[1].strip() if " " in t else "20"
        return ("__CMD__", f"viddemo:{secs}")

    if t.startswith("/vid"):
        secs = t.split(" ", 1)[1].strip() if " " in t else "15"
        return ("__CMD__", f"vid:{secs}")

    if t.startswith("/shot"):
        return ("__CMD__", "shot")

    if t.startswith("/agent"):
        sub = t[len("/agent"):].strip().lower()
        if sub in ("on", "off", "reset", "status", "memory", "memory:clear"):
            return ("__CMD__", f"agent:{sub}")
        return ("__CMD__", "agent:?")

    if t.startswith("/undo"):
        return ("__CMD__", "undo")

    # Fixed: re.DOTALL so multi-line DMs work with @workspace
    m = re.match(r"@(\S+)\s*(.*)", t, re.DOTALL)
    if m:
        return (m.group(1).strip(), m.group(2).strip())

    return (default_ws, t)


def slugify_app_id(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "", name)
    return s.lower()


def safe_replace_in_file(path: Path, replacements: list):
    if not path.exists() or not path.is_file():
        return
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return
    new_text = text
    for a, b in replacements:
        new_text = new_text.replace(a, b)
    if new_text != text:
        path.write_text(new_text)


def replace_package_dirs(root: Path, old_pkg: str, new_pkg: str):
    old_rel = Path(*old_pkg.split("."))
    new_rel = Path(*new_pkg.split("."))

    candidate_roots = [
        root / "app/src/main/java",
        root / "app/src/main/kotlin",
        root / "app/src/test/java",
        root / "app/src/test/kotlin",
        root / "app/src/androidTest/java",
        root / "app/src/androidTest/kotlin",
    ]

    for base in candidate_roots:
        old_path = base / old_rel
        if old_path.exists():
            new_path = base / new_rel
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))


async def ensure_emulator_running() -> str:
    rc, out, err = await run_proc([ADB_BIN, "devices"], cwd=BASE_PROJECTS_DIR, timeout_s=10)
    devices = out.decode(errors="ignore")
    connected = [l.strip() for l in devices.splitlines() if "\tdevice" in l]
    if connected:
        return f"✅ Device ready: {connected[0].split()[0]}"

    if not ANDROID_AVD:
        return "❌ No device connected and ANDROID_AVD not set."

    # Start emulator (best effort)
    await run_proc([EMULATOR_BIN, "-avd", ANDROID_AVD, "-no-snapshot", "-no-boot-anim"], cwd=BASE_PROJECTS_DIR, timeout_s=2)

    rc, out, err = await run_proc([ADB_BIN, "wait-for-device"], cwd=BASE_PROJECTS_DIR, timeout_s=180)
    if rc != 0:
        return f"❌ Emulator failed to start:\n{err.decode(errors='ignore')[:800]}"
    return f"✅ Emulator started: {ANDROID_AVD}"


async def adb_screenshot_to_file(dest: Path) -> tuple:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        ADB_BIN, "exec-out", "screencap", "-p",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0 or not out:
        return False, f"adb screencap failed: {err.decode(errors='ignore')[:800]}"
    dest.write_bytes(out)
    return True, f"✅ Screenshot saved: {dest}"


async def adb_record_video(seconds: int, dest: Path) -> tuple:
    seconds = max(1, min(seconds, 30))  # keep Discord-friendly
    remote = "/sdcard/discord_demo.mp4"

    rc, out, err = await run_proc([ADB_BIN, "shell", "screenrecord", "--time-limit", str(seconds), remote],
                                  cwd=BASE_PROJECTS_DIR, timeout_s=seconds + 10)
    if rc != 0:
        return False, f"screenrecord failed: {err.decode(errors='ignore')[:800]}"

    rc, out, err = await run_proc([ADB_BIN, "pull", remote, str(dest)], cwd=BASE_PROJECTS_DIR, timeout_s=60)
    await run_proc([ADB_BIN, "shell", "rm", remote], cwd=BASE_PROJECTS_DIR, timeout_s=10)

    if rc != 0 or not dest.exists():
        return False, f"pull failed: {err.decode(errors='ignore')[:800]}"
    return True, f"✅ Video saved: {dest}"


def infer_template_dir() -> Path:
    """
    Supports either:
      templates/android/ (contains gradlew, app, settings.gradle.kts)
    OR:
      templates/android/AndroidTemplate/ (your current copy)
    """
    base = (TEMPLATES_DIR / "android").expanduser()
    if (base / "gradlew").exists() and (base / "app").exists():
        return base
    if (base / "AndroidTemplate" / "gradlew").exists():
        return base / "AndroidTemplate"
    return base


def infer_application_id(repo: Path, fallback: Optional[str] = None) -> Optional[str]:
    kts = repo / "app/build.gradle.kts"
    groovy = repo / "app/build.gradle"
    if kts.exists():
        t = kts.read_text(errors="ignore")
        m = re.search(r'applicationId\s*=\s*"([^"]+)"', t)
        if m:
            return m.group(1)
    if groovy.exists():
        t = groovy.read_text(errors="ignore")
        m = re.search(r'applicationId\s+"([^"]+)"', t)
        if m:
            return m.group(1)
    return fallback


# ===== Discord handler =====
@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    if ALLOWED_USER_ID and message.author.id != ALLOWED_USER_ID:
        await message.channel.send("Not authorized.")
        return

    workspaces = load_workspaces()
    default_ws = user_default_ws.get(message.author.id, DEFAULT_WORKSPACE)

    ws, payload = parse(message.content, default_ws)

    # ---- Commands ----
    if ws == "__CMD__":
        if payload == "help":
            await message.channel.send(
                "Commands:\n"
                "/ls — list workspaces\n"
                "/use <ws> — set default workspace\n"
                "/where — show default workspace\n"
                "/run <cmd> — run a single command in default workspace (agent mode)\n"
                "/create android <AppName> — create a new Android app from template\n"
                "/demo — build+install+launch+screenshot in default workspace\n"
                "/shot — screenshot from connected device/emulator\n"
                "/vid [seconds] — build+install+launch+record video (default 15s, max 30s)\n"
                "/viddemo [seconds] — same as /vid but 10–20s range (default 20s) with summary\n"
                "/agent on|off|reset|status|memory|memory:clear — manage the AI agent session (requires AGENT_MODE=1)\n"
                "/undo — restore workspace to state before last agent run\n"
                "Or: @workspace <prompt> — ask Claude in that repo (bypasses agent)"
            )
            return

        if payload == "ls":
            keys = ", ".join(sorted(workspaces.keys())) or "(none)"
            await message.channel.send(f"Workspaces: {keys}")
            return

        if payload == "where":
            await message.channel.send(f"Default workspace: {default_ws or '(not set)'}")
            return

        if payload.startswith("use:"):
            new_ws = payload.split(":", 1)[1]
            if new_ws not in workspaces:
                await message.channel.send(f"Unknown workspace '{new_ws}'. Try /ls")
                return
            user_default_ws[message.author.id] = new_ws
            await message.channel.send(f"✅ Default workspace set to '{new_ws}'")
            return

        if payload.startswith("run:"):
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return
            cmd = payload.split(":", 1)[1].strip()
            if not default_ws or default_ws not in workspaces:
                await message.channel.send("❌ Set a default workspace first with /use <ws> or use @workspace <prompt>.")
                return
            cwd = Path(workspaces[default_ws]).expanduser()
            if not cwd.exists():
                await message.channel.send(f"❌ Workspace path not found: {cwd}")
                return

            await message.channel.send(f"⚙️ Running in **{default_ws}**: `{cmd}`")
            result = await run_shell_command(cmd, cwd=cwd)
            for part in chunk_for_discord(result):
                await message.channel.send(part)
            return

        if payload.startswith("create:"):
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return

            args = payload.split(":", 1)[1].strip().split()
            if len(args) < 2:
                await message.channel.send("Usage: /create android <AppName>")
                return

            kind, name = args[0], args[1]
            if kind != "android":
                await message.channel.send("Only supported right now: /create android <AppName>")
                return

            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,40}", name):
                await message.channel.send("❌ App name must start with a letter and be 2–41 chars (letters/numbers/_).")
                return

            target_dir = (BASE_PROJECTS_DIR / name).expanduser()
            if target_dir.exists():
                await message.channel.send(f"❌ Folder already exists: `{target_dir}`")
                return

            if not is_under_base(target_dir, BASE_PROJECTS_DIR):
                await message.channel.send("❌ Refusing: target not under BASE_PROJECTS_DIR.")
                return

            template_dir = infer_template_dir()
            if not template_dir.exists():
                await message.channel.send(f"❌ Android template not found. Looked for `{template_dir}`.")
                return
            if not (template_dir / "gradlew").exists():
                await message.channel.send(f"❌ Template missing gradlew at `{template_dir}`.")
                return

            await message.channel.send(f"🧱 Creating Android app **{name}** from template…")

            shutil.copytree(str(template_dir), str(target_dir))

            slug = slugify_app_id(name)
            new_pkg = f"{ANDROID_PACKAGE_PREFIX}.{slug}"
            old_pkg = TEMPLATE_OLD_PKG

            safe_replace_in_file(target_dir / "settings.gradle.kts", [("rootProject.name = \"AndroidTemplate\"", f"rootProject.name = \"{name}\"")])
            safe_replace_in_file(target_dir / "settings.gradle", [("rootProject.name = 'AndroidTemplate'", f"rootProject.name = '{name}'")])

            safe_replace_in_file(
                target_dir / "app/build.gradle.kts",
                [
                    (f"namespace = \"{old_pkg}\"", f"namespace = \"{new_pkg}\""),
                    (f"applicationId = \"{old_pkg}\"", f"applicationId = \"{new_pkg}\""),
                    (old_pkg, new_pkg),
                ],
            )
            safe_replace_in_file(
                target_dir / "app/build.gradle",
                [
                    (f"namespace \"{old_pkg}\"", f"namespace \"{new_pkg}\""),
                    (f"applicationId \"{old_pkg}\"", f"applicationId \"{new_pkg}\""),
                    (old_pkg, new_pkg),
                ],
            )

            safe_replace_in_file(target_dir / "app/src/main/AndroidManifest.xml", [(old_pkg, new_pkg)])
            for p in target_dir.rglob("*.kt"):
                safe_replace_in_file(p, [(f"package {old_pkg}", f"package {new_pkg}"), (old_pkg, new_pkg)])
            for p in target_dir.rglob("*.java"):
                safe_replace_in_file(p, [(f"package {old_pkg};", f"package {new_pkg};"), (old_pkg, new_pkg)])

            replace_package_dirs(target_dir, old_pkg, new_pkg)

            key = name.lower()
            workspaces[key] = str(target_dir)
            save_workspaces(workspaces)

            await message.channel.send(
                f"✅ Created `{target_dir}`\n"
                f"• package: `{new_pkg}`\n"
                f"• workspace key: `{key}` added to workspaces.json\n\n"
                f"Next:\n"
                f"`/use {key}` then `/demo`"
            )
            return

        if payload == "shot":
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return

            status = await ensure_emulator_running()
            await message.channel.send(status)

            tmp = Path(tempfile.gettempdir()) / "discord_bot_screen.png"
            ok, msg = await adb_screenshot_to_file(tmp)
            if not ok:
                await message.channel.send(f"❌ {msg}")
                return

            await message.channel.send(file=discord.File(str(tmp)))
            return

        if payload == "demo":
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return

            if not default_ws or default_ws not in workspaces:
                await message.channel.send("❌ Set a default workspace first with /use <ws>.")
                return

            repo = Path(workspaces[default_ws]).expanduser()
            if not repo.exists():
                await message.channel.send(f"❌ Workspace path not found: {repo}")
                return

            status = await ensure_emulator_running()
            await message.channel.send(status)

            await message.channel.send(f"🏗️ Building+installing in **{default_ws}**…")
            rc, out, err = await run_proc(["bash", "-lc", "./gradlew installDebug"], cwd=repo, timeout_s=1200)
            if rc != 0:
                await message.channel.send(
                    f"❌ Build/install failed (exit {rc}).\n"
                    f"STDERR:\n{err.decode(errors='ignore')[:1800]}"
                )
                return

            pkg = infer_application_id(repo)
            if not pkg:
                await message.channel.send("⚠️ Installed, but couldn't infer applicationId to launch.")
            else:
                await message.channel.send(f"🚀 Launching `{pkg}`…")
                await run_proc([ADB_BIN, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], cwd=repo, timeout_s=30)
                await asyncio.sleep(2)

            tmp = Path(tempfile.gettempdir()) / "discord_bot_demo.png"
            ok, msg = await adb_screenshot_to_file(tmp)
            if not ok:
                await message.channel.send(f"❌ {msg}")
                return

            await message.channel.send(f"📸 Demo screenshot for **{default_ws}**:")
            await message.channel.send(file=discord.File(str(tmp)))
            return

        if payload.startswith("vid:"):
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return

            if not default_ws or default_ws not in workspaces:
                await message.channel.send("❌ Set a default workspace first with /use <ws>.")
                return

            repo = Path(workspaces[default_ws]).expanduser()
            if not repo.exists():
                await message.channel.send(f"❌ Workspace path not found: {repo}")
                return

            try:
                secs = int(payload.split(":", 1)[1])
            except Exception:
                secs = 15
            secs = max(1, min(secs, 30))

            status = await ensure_emulator_running()
            await message.channel.send(status)
            if status.startswith("❌"):
                return

            await message.channel.send(f"🏗️ Building+installing in **{default_ws}**…")
            rc, out, err = await run_proc(["bash", "-lc", "./gradlew installDebug"], cwd=repo, timeout_s=1200)
            if rc != 0:
                await message.channel.send(
                    f"❌ Build/install failed (exit {rc}).\n"
                    f"STDERR:\n{err.decode(errors='ignore')[:1800]}"
                )
                return

            pkg = infer_application_id(repo)
            if not pkg:
                await message.channel.send("⚠️ Installed, but couldn't infer applicationId to launch.")
            else:
                await message.channel.send(f"🚀 Launching `{pkg}`…")
                await run_proc([ADB_BIN, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], cwd=repo, timeout_s=30)
                await asyncio.sleep(2)

            await message.channel.send(f"🎬 Recording {secs}s of **{default_ws}**…")
            tmp = Path(tempfile.gettempdir()) / "discord_bot_demo.mp4"
            ok, msg = await adb_record_video(secs, tmp)
            if not ok:
                await message.channel.send(f"❌ {msg}")
                return

            await message.channel.send(file=discord.File(str(tmp)))
            return

        if payload.startswith("viddemo:"):
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return

            if not default_ws or default_ws not in workspaces:
                await message.channel.send("❌ Set a default workspace first with /use <ws>.")
                return

            repo = Path(workspaces[default_ws]).expanduser()
            if not repo.exists():
                await message.channel.send(f"❌ Workspace path not found: {repo}")
                return

            try:
                secs = int(payload.split(":", 1)[1])
            except Exception:
                secs = 20
            secs = max(10, min(secs, 20))

            status = await ensure_emulator_running()
            await message.channel.send(status)
            if status.startswith("❌"):
                return

            await message.channel.send(f"🏗️ Building+installing in **{default_ws}**…")
            rc, out, err = await run_proc(["bash", "-lc", "./gradlew installDebug"], cwd=repo, timeout_s=1200)
            if rc != 0:
                await message.channel.send(
                    f"❌ Build/install failed (exit {rc}).\n"
                    f"STDERR:\n{err.decode(errors='ignore')[:1800]}"
                )
                return

            pkg = infer_application_id(repo)
            if not pkg:
                await message.channel.send("⚠️ Installed, but couldn't infer applicationId to launch.")
            else:
                await message.channel.send(f"🚀 Launching `{pkg}`…")
                await run_proc([ADB_BIN, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], cwd=repo, timeout_s=30)
                await asyncio.sleep(2)

            await message.channel.send(f"🎬 Recording demo video ({secs}s) of **{default_ws}**…")
            tmp = Path(tempfile.gettempdir()) / "discord_bot_viddemo.mp4"
            ok, msg = await adb_record_video(secs, tmp)
            if not ok:
                await message.channel.send(f"❌ {msg}")
                return

            summary_pkg = pkg or "(unknown)"
            await message.channel.send(
                f"✅ Demo video recorded.\n"
                f"• workspace: `{default_ws}`\n"
                f"• appId: `{summary_pkg}`\n"
                f"• duration: {secs}s"
            )
            await message.channel.send(file=discord.File(str(tmp)))
            return

        # ---- /agent subcommands ----
        if payload.startswith("agent:"):
            sub = payload.split(":", 1)[1]

            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1 in your .env.")
                return

            a_state = get_agent_state(message.author.id)

            if sub == "on":
                a_state["enabled"] = True
                ws_info = f" (workspace: **{default_ws}**)" if default_ws else " (no workspace set — use /use <ws>)"
                await message.channel.send(
                    f"✅ Agent ON{ws_info}.\n"
                    "Text freely to have Claude edit your code. `@workspace prompt` still goes to Claude directly."
                )
                return

            if sub == "off":
                a_state["enabled"] = False
                await message.channel.send("✅ Agent OFF. Back to normal @workspace Claude prompts.")
                return

            if sub == "reset":
                a_state["context"] = []
                a_state["last_cmds"] = []
                a_state["last_run"] = None
                await message.channel.send("✅ Agent context cleared.")
                return

            if sub == "status":
                enabled_str = "ON" if a_state["enabled"] else "OFF"
                busy_str = "yes" if a_state["busy"] else "no"
                ctx_pairs = len(a_state["context"]) // 2
                last_run_str = (
                    a_state["last_run"].strftime("%Y-%m-%d %H:%M:%S")
                    if a_state["last_run"] else "never"
                )
                cmds = a_state["last_cmds"] or []
                cmds_str = ", ".join(f"`{c}`" for c, _ in cmds) or "none"
                backup_counts = {
                    k: len(v) for k, v in a_state.get("ws_backups", {}).items() if v
                }
                lines = [
                    f"Agent: {enabled_str}",
                    f"Busy: {busy_str}",
                    f"Workspace: {default_ws or '(not set)'}",
                    f"Last run: {last_run_str}",
                    f"Last commands: {cmds_str}",
                    f"Context: {ctx_pairs}/{MAX_AGENT_CONTEXT} pairs",
                ]
                if backup_counts:
                    lines.append("Backups: " + ", ".join(f"{k}:{n}" for k, n in backup_counts.items()))
                mem_key = _mem_key(message.author.id, default_ws)
                mem_count = len(_agent_memory.get(mem_key, []))
                lines.append(f"Memory: {mem_count}/{MAX_AGENT_MEMORY} entries")
                await message.channel.send("\n".join(lines))
                return

            if sub == "memory":
                ws_key = default_ws
                if ws_key and ws_key in workspaces:
                    load_agent_memory(
                        message.author.id, ws_key,
                        Path(workspaces[ws_key]).expanduser()
                    )
                mem_key = _mem_key(message.author.id, ws_key or "")
                entries = _agent_memory.get(mem_key, [])
                if not entries:
                    await message.channel.send("No persistent memory entries yet.")
                else:
                    lines = [f"Memory ({len(entries)}/{MAX_AGENT_MEMORY} entries):"]
                    for i, e in enumerate(entries, 1):
                        lines.append(f"{i}. [{e['user'][:80]}] → {e['summary'][:120]}")
                    await message.channel.send("\n".join(lines))
                return

            if sub == "memory:clear":
                ws_key = default_ws
                mem_key = _mem_key(message.author.id, ws_key or "")
                _agent_memory.pop(mem_key, None)
                _memory_loaded.discard(mem_key)
                if ws_key and ws_key in workspaces:
                    repo_path = Path(workspaces[ws_key]).expanduser()
                    save_agent_memory(message.author.id, ws_key, repo_path)
                await message.channel.send("✅ Persistent memory cleared.")
                return

            # sub == "?" — unrecognized subcommand
            await message.channel.send("Usage: /agent on | off | reset | status | memory | memory:clear")
            return

        # ---- /undo ----
        if payload == "undo":
            if not AGENT_MODE:
                await message.channel.send("❌ Agent mode is disabled. Set AGENT_MODE=1.")
                return

            a_state = get_agent_state(message.author.id)
            ws_key = user_default_ws.get(message.author.id, DEFAULT_WORKSPACE)

            if not ws_key or ws_key not in workspaces:
                await message.channel.send("❌ No default workspace set. Use /use <ws> first.")
                return

            ws_backups = a_state["ws_backups"].get(ws_key, [])
            if not ws_backups:
                await message.channel.send("❌ No agent backups found for this workspace.")
                return

            backup_dir_str = ws_backups.pop()
            a_state["ws_backups"][ws_key] = ws_backups
            backup_dir = Path(backup_dir_str)

            repo_path = Path(workspaces[ws_key]).expanduser()
            await message.channel.send(f"↩️ Restoring from backup `{backup_dir.name}`…")
            ok, msg = restore_backup(backup_dir, repo_path)
            emoji = "✅" if ok else "⚠️"
            await message.channel.send(f"{emoji} {msg}")
            return

        # Unknown command payload
        await message.channel.send("Unknown command. Try /help")
        return

    # ---- Normal Claude prompt / Agent instruction ----
    if not ws:
        await message.channel.send("No workspace set. Use /ls then /use <name> or start with @workspace.")
        return
    if ws not in workspaces:
        await message.channel.send(f"Unknown workspace '{ws}'. Try /ls")
        return
    if not payload:
        await message.channel.send("Send a prompt after the workspace, e.g. `@quickcut find the upload code`")
        return

    repo_path = Path(workspaces[ws]).expanduser()
    if not repo_path.exists():
        await message.channel.send(f"❌ Workspace path not found: {repo_path}")
        return

    # Route to agent if enabled and this was plain text (not @workspace)
    is_default_ws_route = (ws == user_default_ws.get(message.author.id, DEFAULT_WORKSPACE))
    a_state = get_agent_state(message.author.id)

    if AGENT_MODE and a_state["enabled"] and is_default_ws_route:
        if a_state["busy"]:
            await message.channel.send("⏳ Agent is busy with a previous request. Please wait.")
            return
        a_state["busy"] = True
        try:
            await run_agent(payload, message.author.id, ws, repo_path, message.channel)
        finally:
            a_state["busy"] = False
        return

    # Direct Claude mode (agent off, or @workspace explicit)
    await message.channel.send(f"🧠 Working in **{ws}**…")
    reply = await run_claude(payload, repo_path)
    for part in chunk_for_discord(reply):
        await message.channel.send(part)


client.run(TOKEN)
