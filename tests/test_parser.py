"""Unit tests for parser.py parse() — the Discord message grammar.

parse() returns one of: WorkspacePrompt | Command | FallbackPrompt.
"""

import pytest

from parser import parse, WorkspacePrompt, Command, FallbackPrompt


# ── @workspace prompts ────────────────────────────────────────────────────────

def test_workspace_prompt_basic():
    r = parse("@myapp add a login screen")
    assert isinstance(r, WorkspacePrompt)
    assert r.workspace == "myapp"
    assert r.prompt == "add a login screen"


def test_workspace_name_lowercased_prompt_preserves_case():
    r = parse("@MyApp Add A Thing")
    assert isinstance(r, WorkspacePrompt)
    assert r.workspace == "myapp"          # workspace is lowercased
    assert r.prompt == "Add A Thing"       # prompt case preserved


def test_workspace_prompt_multiline():
    r = parse("@myapp line one\nline two")
    assert isinstance(r, WorkspacePrompt)
    assert r.workspace == "myapp"
    assert r.prompt == "line one\nline two"


def test_workspace_with_leading_whitespace_is_stripped():
    r = parse("   @myapp hello  ")
    assert isinstance(r, WorkspacePrompt)
    assert r.workspace == "myapp"
    assert r.prompt == "hello"


# ── fallback prompts ──────────────────────────────────────────────────────────

def test_fallback_plain_text():
    r = parse("just build me something cool")
    assert isinstance(r, FallbackPrompt)
    assert r.prompt == "just build me something cool"


def test_bare_at_without_prompt_is_fallback():
    # "@myapp" with no following prompt text does not match the @ws regex.
    r = parse("@myapp")
    assert isinstance(r, FallbackPrompt)


# ── simple slash commands ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,name", [
    ("/help", "help"),
    ("/ls", "ls"),
    ("/workspaces", "ls"),       # alias
    ("/status", "gitstatus"),
    ("/undo", "undo"),
    ("/done", "done"),
    ("/health", "health"),
    ("/users", "users"),
    ("/admin", "admin"),
    ("/spend", "spend"),
    ("/testflight", "testflight"),
    ("/appraise", "appraise"),
    ("/appcheck", "appraise"),   # alias
    ("/review", "appraise"),     # alias
])
def test_simple_commands(text, name):
    r = parse(text)
    assert isinstance(r, Command)
    assert r.name == name


def test_command_name_case_insensitive():
    r = parse("/HELP")
    assert isinstance(r, Command)
    assert r.name == "help"


def test_unknown_command():
    r = parse("/nonsense foo bar")
    assert isinstance(r, Command)
    assert r.name == "unknown"
    assert r.raw_cmd == "/nonsense foo bar"   # raw_cmd is the full original text


# ── commands carrying raw_cmd ─────────────────────────────────────────────────

def test_buildapp_with_desc():
    r = parse("/buildapp a todo list app")
    assert r.name == "buildapp"
    assert r.raw_cmd == "a todo list app"


def test_buildapp_alias_dash():
    assert parse("/build-app foo").name == "buildapp"


def test_buildapp_no_arg_raw_cmd_none():
    r = parse("/buildapp")
    assert r.name == "buildapp"
    assert r.raw_cmd is None


def test_build_app_alias_via_build():
    # "/build app <desc>" routes to buildapp with the "app" prefix stripped.
    r = parse("/build app my cool thing")
    assert r.name == "buildapp"
    assert r.raw_cmd == "my cool thing"


# ── platform parsing ──────────────────────────────────────────────────────────

def test_build_platform_default_all():
    r = parse("/build")
    assert r.name == "build"
    assert r.platform == "all"


@pytest.mark.parametrize("plat", ["android", "ios", "web", "all"])
def test_build_platform_keyword(plat):
    r = parse(f"/build {plat}")
    assert r.name == "build"
    assert r.platform == plat


def test_build_unknown_platform_falls_back_to_all():
    # _parse_platform returns None for non-PLATFORMS words, then build defaults to "all".
    r = parse("/build banana")
    assert r.name == "build"
    assert r.platform == "all"


def test_demo_platform_none_when_absent():
    r = parse("/demo")
    assert r.name == "demo"
    assert r.platform is None


def test_demo_platform_extracted():
    r = parse("/demo ios")
    assert r.name == "demo"
    assert r.platform == "ios"


def test_platform_command_lowercases_arg():
    r = parse("/platform IOS")
    assert r.name == "platform"
    assert r.platform == "ios"


# ── sub-parsing commands ──────────────────────────────────────────────────────

@pytest.mark.parametrize("sub", ["list", "undo", "redo", "github"])
def test_save_known_subcommands(sub):
    r = parse(f"/save {sub}")
    assert r.name == "save"
    assert r.sub == sub
    assert r.raw_cmd is None


def test_save_custom_message_is_raw_cmd():
    r = parse("/save fixed the colors")
    assert r.name == "save"
    assert r.sub is None
    assert r.raw_cmd == "fixed the colors"


def test_stash_default_push():
    r = parse("/stash")
    assert r.name == "stash"
    assert r.sub == "push"


def test_stash_pop():
    r = parse("/stash pop")
    assert r.name == "stash"
    assert r.sub == "pop"


def test_repo_sub_and_arg():
    r = parse("/repo set owner/name")
    assert r.name == "repo"
    assert r.sub == "set"
    assert r.arg == "owner/name"


def test_data_sub_and_arg():
    r = parse("/data query SELECT 1")
    assert r.name == "data"
    assert r.sub == "query"
    assert r.arg == "SELECT 1"


def test_mirror_default_start():
    r = parse("/mirror")
    assert r.name == "mirror"
    assert r.sub == "start"


def test_showcase_gallery_routes_to_gallery():
    r = parse("/showcase gallery")
    assert r.name == "gallery"


def test_showcase_workspace():
    r = parse("/showcase MyApp")
    assert r.name == "showcase"
    assert r.workspace == "myapp"


def test_tryapp_workspace_and_platform():
    r = parse("/tryapp myapp android")
    assert r.name == "tryapp"
    assert r.workspace == "myapp"
    assert r.platform == "android"


def test_tryapp_invalid_platform_dropped():
    r = parse("/tryapp myapp banana")
    assert r.name == "tryapp"
    assert r.workspace == "myapp"
    assert r.platform is None


def test_deleteapp_workspace_lowercased():
    r = parse("/deleteapp MyApp")
    assert r.name == "deleteapp"
    assert r.workspace == "myapp"


def test_run_and_runsh_raw_cmd():
    assert parse("/run ls -la").raw_cmd == "ls -la"
    assert parse("/runsh ps | grep x").raw_cmd == "ps | grep x"
    assert parse("/run").raw_cmd is None
