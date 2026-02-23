"""
safety.py — Command validation for /run and /runsh.
"""

import re
import shlex

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\b", re.I), re.compile(r"\bsudo\b", re.I),
    re.compile(r"\bcurl\b.*\|\s*\bbash\b", re.I), re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bdd\s+if=", re.I), re.compile(r"\bchmod\s+777\b", re.I),
    re.compile(r"\beval\b", re.I), re.compile(r"\bexec\b", re.I),
]

BLOCKED_OPERATORS = re.compile(r"[|&;<>`$]")

RUNSH_ALLOWLIST = {
    "ps", "pgrep", "grep", "tail", "head", "cat", "ls", "pwd", "whoami",
    "wc", "sort", "uniq", "find", "echo", "date", "uptime", "df", "du",
    "adb", "emulator", "./gradlew", "git", "xcrun", "xcodebuild",
}

SUBCOMMAND_ALLOW = {
    "adb": {"devices", "install", "shell", "exec-out", "pull", "wait-for-device"},
    "./gradlew": {"installDebug", "assembleDebug", "clean", "tasks",
                  "composeApp:installDebug", "composeApp:wasmJsBrowserDistribution",
                  "composeApp:linkDebugFrameworkIosSimulatorArm64"},
    "git": {"status", "diff", "log", "branch", "show", "stash"},
    "xcrun": {"simctl"},
}

RUNSH_BLOCKED_OPS = re.compile(r"(&&|\|\||;|>>?|<|`|\$\(|\$\{)")


def validate_run(cmd):
    if BLOCKED_OPERATORS.search(cmd):
        return "Blocked: shell operators not allowed in /run. Use /runsh for pipes."
    for p in DANGEROUS_PATTERNS:
        if p.search(cmd):
            return f"Blocked: dangerous pattern → `{p.pattern}`"
    return None


def validate_runsh(cmd):
    for p in DANGEROUS_PATTERNS:
        if p.search(cmd):
            return f"Blocked: dangerous pattern → `{p.pattern}`"
    if RUNSH_BLOCKED_OPS.search(cmd):
        return "Blocked: operators like && || ; > < $() ` are not allowed."
    for seg in [s.strip() for s in cmd.split("|")]:
        if not seg:
            return "Blocked: empty pipeline segment."
        try:
            tokens = shlex.split(seg)
        except ValueError as e:
            return f"Blocked: parse error → {e}"
        base = tokens[0]
        if base not in RUNSH_ALLOWLIST:
            return f"Blocked: `{base}` not in allowlist."
        if base in SUBCOMMAND_ALLOW and len(tokens) > 1:
            if tokens[1] not in SUBCOMMAND_ALLOW[base]:
                return f"Blocked: `{base} {tokens[1]}` not allowed."
    return None
