"""
commands/bot_todo.py — Simple markdown-based todo tracker.

/bot-todo <note>     → add a todo
/bot-todo            → list all todos
/bot-todo done <N>   → mark todo #N as done
"""

import re
from pathlib import Path

TODO_FILE = Path(__file__).parent.parent / "bot-todos.md"


def _ensure_file():
    if not TODO_FILE.exists():
        TODO_FILE.write_text("# Bot Todos\n\n")


def _read_todos() -> list[tuple[bool, str]]:
    """Return list of (done, text) tuples."""
    _ensure_file()
    todos = []
    for line in TODO_FILE.read_text().splitlines():
        m = re.match(r"^- \[( |x)\] (.+)$", line)
        if m:
            done = m.group(1) == "x"
            text = m.group(2)
            # Strip strikethrough from done items for clean text
            if done and text.startswith("~~") and text.endswith("~~"):
                text = text[2:-2]
            todos.append((done, text))
    return todos


def _write_todos(todos: list[tuple[bool, str]]):
    lines = ["# Bot Todos\n"]
    for done, text in todos:
        if done:
            lines.append(f"- [x] ~~{text}~~")
        else:
            lines.append(f"- [ ] {text}")
    lines.append("")  # trailing newline
    TODO_FILE.write_text("\n".join(lines))


def handle_bot_todo(raw_args: str | None) -> str:
    """Dispatch /bot-todo subcommands. Returns message string."""
    if not raw_args:
        return _list_todos()

    raw_args = raw_args.strip()

    # /bot-todo done <N>
    m = re.match(r"^done\s+(\d+)$", raw_args, re.IGNORECASE)
    if m:
        return _mark_done(int(m.group(1)))

    # /bot-todo <note> — add a new todo
    return _add_todo(raw_args)


def _list_todos() -> str:
    todos = _read_todos()
    if not todos:
        return "No todos yet. Add one with `/bot-todo <note>`"

    lines = ["**Bot Todos:**"]
    for i, (done, text) in enumerate(todos, 1):
        check = "x" if done else " "
        display = f"~~{text}~~" if done else text
        lines.append(f"  {i}. [{check}] {display}")

    pending = sum(1 for done, _ in todos if not done)
    lines.append(f"\n{pending} pending / {len(todos)} total")
    return "\n".join(lines)


def _add_todo(note: str) -> str:
    todos = _read_todos()
    todos.append((False, note))
    _write_todos(todos)
    return f"Added todo #{len(todos)}: {note}"


def _mark_done(n: int) -> str:
    todos = _read_todos()
    if n < 1 or n > len(todos):
        return f"Invalid todo number. Use 1–{len(todos)}."
    idx = n - 1
    if todos[idx][0]:
        return f"Todo #{n} is already done."
    todos[idx] = (True, todos[idx][1])
    _write_todos(todos)
    return f"Marked #{n} as done: ~~{todos[idx][1]}~~"
