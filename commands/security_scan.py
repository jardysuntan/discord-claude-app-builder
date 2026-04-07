"""
commands/security_scan.py — Pre-publish security scan for AI-generated code.

Checks for OWASP-style vulnerabilities before TestFlight/Play Store publish:
- Hardcoded secrets
- Insecure network calls (HTTP vs HTTPS)
- SQL injection in local DB queries
- Missing input validation
- Overly broad permissions in AndroidManifest/Info.plist
- Claude self-audit of generated code

Used as security gate in /testflight and /playstore flows.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent_protocol import AgentRunner

log = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────

SECRET_PATTERNS = [
    (r'AIza[0-9A-Za-z\-_]{35}', "Google API key"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
    (r'"sk-[a-zA-Z0-9]{20,}"', "OpenAI API key"),
    (r'"ghp_[a-zA-Z0-9]{36}"', "GitHub token"),
    (r'(?:firebase|supabase)[_-]?(?:key|secret|token|url)\s*=\s*"[^"]{10,}"',
     "Firebase/Supabase credential"),
    (r'(?:api_key|secret_key|api_secret|password|auth_token)\s*=\s*"[^"]{10,}"',
     "Hardcoded secret"),
    (r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', "Hardcoded bearer token"),
]

SQL_INJECTION_PATTERNS = [
    (r'rawQuery\s*\(\s*["\'].*?\$\{', "String interpolation in rawQuery"),
    (r'rawQuery\s*\(\s*["\'].*?\+\s*\w', "String concatenation in rawQuery"),
    (r'execSQL\s*\(\s*["\'].*?\$\{', "String interpolation in execSQL"),
    (r'execSQL\s*\(\s*["\'].*?\+\s*\w', "String concatenation in execSQL"),
    (r'query\s*\(\s*["\']SELECT.*?\+\s*\w', "String concatenation in SQL query"),
    (r'statement\.execute\s*\(\s*["\'].*?\$\{', "String interpolation in SQL execute"),
]

BROAD_ANDROID_PERMISSIONS = [
    ("android.permission.READ_CONTACTS", "Read contacts"),
    ("android.permission.WRITE_CONTACTS", "Write contacts"),
    ("android.permission.READ_CALL_LOG", "Read call log"),
    ("android.permission.CAMERA", "Camera access"),
    ("android.permission.RECORD_AUDIO", "Microphone access"),
    ("android.permission.ACCESS_FINE_LOCATION", "Precise location"),
    ("android.permission.ACCESS_BACKGROUND_LOCATION", "Background location"),
    ("android.permission.READ_SMS", "Read SMS"),
    ("android.permission.SEND_SMS", "Send SMS"),
    ("android.permission.READ_EXTERNAL_STORAGE", "Read storage"),
    ("android.permission.WRITE_EXTERNAL_STORAGE", "Write storage"),
    ("android.permission.MANAGE_EXTERNAL_STORAGE", "Manage all files"),
    ("android.permission.SYSTEM_ALERT_WINDOW", "Draw over other apps"),
]


# ── Data types ─────────────────────────────────────────────────────────────

@dataclass
class SecurityFinding:
    check_id: str
    severity: str       # critical, warning, info
    title: str
    detail: str
    fix_hint: str
    file_path: str = ""  # relative path where issue was found


@dataclass
class SecurityScanResult:
    findings: list[SecurityFinding] = field(default_factory=list)
    claude_findings: list[dict] = field(default_factory=list)
    scan_time_s: float = 0.0
    has_critical: bool = False

    @property
    def should_block(self) -> bool:
        """True if any critical finding exists."""
        if self.has_critical:
            return True
        for cf in self.claude_findings:
            if cf.get("severity") == "critical":
                return True
        return False


# ── Helpers ────────────────────────────────────────────────────────────────

def _scan_source_files(root: Path) -> list[tuple[Path, str]]:
    """Read source files from workspace. Returns (path, content) pairs."""
    files = []
    for src_dir in ["composeApp/src", "iosApp/iosApp", "shared/src"]:
        src_path = root / src_dir
        if not src_path.exists():
            continue
        for fp in src_path.rglob("*"):
            if fp.suffix not in (".kt", ".swift", ".kts"):
                continue
            if any(s in fp.parts for s in ("build", ".gradle", "test", "Test", "androidTest")):
                continue
            if fp.stat().st_size > 100_000:
                continue
            try:
                files.append((fp, fp.read_text(errors="ignore")))
            except (OSError, PermissionError):
                continue
            if len(files) >= 200:
                break
    return files


def _read_if_exists(path: Path) -> Optional[str]:
    if path.exists():
        try:
            return path.read_text(errors="ignore")
        except (OSError, PermissionError):
            pass
    return None


# ── Checks ─────────────────────────────────────────────────────────────────

def _check_hardcoded_secrets(
    root: Path, source_files: list[tuple[Path, str]],
) -> list[SecurityFinding]:
    """Scan for hardcoded secrets and API keys in source code."""
    findings = []
    hits: list[tuple[str, str]] = []

    for fp, content in source_files:
        rel = str(fp.relative_to(root))
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                hits.append((rel, label))
                break

    if hits:
        files_str = ", ".join(f"`{f}` ({label})" for f, label in hits[:5])
        findings.append(SecurityFinding(
            check_id="hardcoded_secrets",
            severity="critical",
            title=f"Hardcoded secrets in {len(hits)} file(s)",
            detail=f"Potential secrets found: {files_str}. "
                   "Exposed API keys can be extracted from published app binaries.",
            fix_hint="Move secrets to local.properties, environment variables, or a "
                     "secrets manager. Never ship API keys in source code.",
            file_path=hits[0][0] if hits else "",
        ))
    return findings


def _check_insecure_network(
    root: Path, source_files: list[tuple[Path, str]],
) -> list[SecurityFinding]:
    """Check for HTTP (non-HTTPS) network calls."""
    findings = []
    http_hits: list[tuple[str, str]] = []

    for fp, content in source_files:
        rel = str(fp.relative_to(root))
        for m in re.finditer(
            r'http://(?!localhost|127\.0\.0\.1|10\.0\.)[^\s"\'<>]+', content,
        ):
            http_hits.append((rel, m.group(0)[:60]))

    if http_hits:
        files_str = ", ".join(f"`{f}`" for f, _ in http_hits[:3])
        findings.append(SecurityFinding(
            check_id="insecure_http",
            severity="critical",
            title=f"Insecure HTTP URL(s) in {len(http_hits)} location(s)",
            detail=f"Non-HTTPS URLs found in: {files_str}. "
                   "Data sent over HTTP can be intercepted (MITM attacks). "
                   "Both app stores require HTTPS.",
            fix_hint="Change all http:// URLs to https://.",
            file_path=http_hits[0][0] if http_hits else "",
        ))
    return findings


def _check_sql_injection(
    root: Path, source_files: list[tuple[Path, str]],
) -> list[SecurityFinding]:
    """Check for SQL injection vulnerabilities in local DB queries."""
    findings = []
    hits: list[tuple[str, str]] = []

    for fp, content in source_files:
        rel = str(fp.relative_to(root))
        for pattern, label in SQL_INJECTION_PATTERNS:
            if re.search(pattern, content):
                hits.append((rel, label))
                break

    if hits:
        files_str = ", ".join(f"`{f}` ({label})" for f, label in hits[:3])
        findings.append(SecurityFinding(
            check_id="sql_injection",
            severity="critical",
            title=f"Potential SQL injection in {len(hits)} file(s)",
            detail=f"Unsafe SQL construction found: {files_str}. "
                   "User input concatenated into SQL queries can allow data theft or corruption.",
            fix_hint="Use parameterized queries (? placeholders) instead of string "
                     "concatenation or interpolation in SQL statements.",
        ))
    return findings


def _check_input_validation(
    root: Path, source_files: list[tuple[Path, str]],
) -> list[SecurityFinding]:
    """Check for missing input validation on user-facing inputs."""
    findings = []
    webview_files: list[str] = []
    deeplink_files: list[str] = []

    for fp, content in source_files:
        rel = str(fp.relative_to(root))

        # WebView with JS enabled and no URL validation
        if re.search(r'WebView|WKWebView|evaluateJavascript', content):
            if re.search(r'javaScriptEnabled\s*=\s*true|allowsInlineMediaPlayback', content):
                if not re.search(r'(?:allowList|whitelist|trusted|validate)', content, re.IGNORECASE):
                    webview_files.append(rel)

        # Deep links without validation
        if re.search(r'<data\s+android:scheme|handleDeepLink|onOpenURL', content):
            if not re.search(r'(?:validate|sanitize|verify|check)', content, re.IGNORECASE):
                deeplink_files.append(rel)

    if webview_files:
        findings.append(SecurityFinding(
            check_id="webview_no_validation",
            severity="warning",
            title=f"WebView with JS enabled lacks URL validation",
            detail=f"WebView in {', '.join(webview_files[:3])} has JavaScript enabled "
                   "without apparent URL allowlisting. This could allow loading untrusted content.",
            fix_hint="Add URL validation/allowlisting before loading content in WebView.",
            file_path=webview_files[0] if webview_files else "",
        ))

    if deeplink_files:
        findings.append(SecurityFinding(
            check_id="deeplink_no_validation",
            severity="warning",
            title=f"Deep link handler lacks input validation",
            detail=f"Deep link handling in {', '.join(deeplink_files[:3])} "
                   "without apparent input validation.",
            fix_hint="Validate and sanitize all data received via deep links.",
            file_path=deeplink_files[0] if deeplink_files else "",
        ))

    return findings


def _check_android_permissions(root: Path) -> list[SecurityFinding]:
    """Check for overly broad permissions in AndroidManifest.xml."""
    manifest = root / "composeApp" / "src" / "androidMain" / "AndroidManifest.xml"
    text = _read_if_exists(manifest)
    if not text:
        return []

    broad_found = []
    for perm, label in BROAD_ANDROID_PERMISSIONS:
        if perm in text:
            broad_found.append(label)

    if len(broad_found) >= 3:
        return [SecurityFinding(
            check_id="broad_android_permissions",
            severity="warning",
            title=f"{len(broad_found)} sensitive Android permissions declared",
            detail=f"Permissions: {', '.join(broad_found)}. "
                   "Google Play flags apps with unnecessary sensitive permissions. "
                   "Each requires justification in the Play Console declaration.",
            fix_hint="Remove permissions not needed by the app. Request sensitive "
                     "permissions at runtime with clear user justification.",
            file_path="composeApp/src/androidMain/AndroidManifest.xml",
        )]
    return []


def _check_ios_plist_permissions(root: Path) -> list[SecurityFinding]:
    """Check for permission descriptions in Info.plist (Apple requires usage strings)."""
    plist = root / "iosApp" / "iosApp" / "Info.plist"
    text = _read_if_exists(plist)
    if not text:
        return []

    findings = []

    # Check for permission keys without proper usage description
    perm_keys = [
        ("NSCameraUsageDescription", "Camera"),
        ("NSMicrophoneUsageDescription", "Microphone"),
        ("NSLocationWhenInUseUsageDescription", "Location"),
        ("NSLocationAlwaysUsageDescription", "Always-on location"),
        ("NSPhotoLibraryUsageDescription", "Photo library"),
        ("NSContactsUsageDescription", "Contacts"),
    ]

    declared = []
    empty_desc = []
    for key, label in perm_keys:
        if key in text:
            declared.append(label)
            # Check if description is empty
            m = re.search(rf'<key>{key}</key>\s*<string>\s*</string>', text)
            if m:
                empty_desc.append(label)

    if empty_desc:
        findings.append(SecurityFinding(
            check_id="ios_empty_permission_desc",
            severity="critical",
            title=f"Empty permission description(s) in Info.plist",
            detail=f"Empty usage descriptions for: {', '.join(empty_desc)}. "
                   "Apple rejects apps with empty or generic permission strings.",
            fix_hint="Add meaningful usage descriptions explaining why the app "
                     "needs each permission.",
            file_path="iosApp/iosApp/Info.plist",
        ))

    if len(declared) >= 4:
        findings.append(SecurityFinding(
            check_id="ios_many_permissions",
            severity="warning",
            title=f"{len(declared)} sensitive permissions declared",
            detail=f"Permissions: {', '.join(declared)}. Apple scrutinizes apps "
                   "requesting many permissions. Ensure each is necessary.",
            fix_hint="Remove permission keys from Info.plist for features the app doesn't use.",
            file_path="iosApp/iosApp/Info.plist",
        ))

    return findings


def _check_cleartext_and_ats(root: Path) -> list[SecurityFinding]:
    """Check for cleartext traffic (Android) and ATS bypass (iOS)."""
    findings = []

    manifest = root / "composeApp" / "src" / "androidMain" / "AndroidManifest.xml"
    text = _read_if_exists(manifest)
    if text and 'usesCleartextTraffic="true"' in text:
        findings.append(SecurityFinding(
            check_id="cleartext_traffic",
            severity="warning",
            title="Cleartext HTTP traffic enabled",
            detail='AndroidManifest has usesCleartextTraffic="true" — allows '
                   "unencrypted HTTP traffic, exposing data to interception.",
            fix_hint='Remove or set android:usesCleartextTraffic="false".',
            file_path="composeApp/src/androidMain/AndroidManifest.xml",
        ))

    plist = root / "iosApp" / "iosApp" / "Info.plist"
    text = _read_if_exists(plist)
    if text and "NSAllowsArbitraryLoads" in text and "<true/>" in text:
        findings.append(SecurityFinding(
            check_id="ats_bypass",
            severity="warning",
            title="App Transport Security bypassed",
            detail="Info.plist disables HTTPS enforcement globally. "
                   "All network traffic can use unencrypted HTTP.",
            fix_hint="Remove NSAllowsArbitraryLoads or add per-domain exceptions.",
            file_path="iosApp/iosApp/Info.plist",
        ))

    return findings


# ── Deterministic scan orchestrator ────────────────────────────────────────

def run_security_checks(ws_path: str) -> list[SecurityFinding]:
    """Run all deterministic security checks. Returns list of findings."""
    root = Path(ws_path)
    source_files = _scan_source_files(root)
    findings: list[SecurityFinding] = []

    findings.extend(_check_hardcoded_secrets(root, source_files))
    findings.extend(_check_insecure_network(root, source_files))
    findings.extend(_check_sql_injection(root, source_files))
    findings.extend(_check_input_validation(root, source_files))
    findings.extend(_check_android_permissions(root))
    findings.extend(_check_ios_plist_permissions(root))
    findings.extend(_check_cleartext_and_ats(root))

    return findings


# ── Claude self-audit ──────────────────────────────────────────────────────

SECURITY_AUDIT_PROMPT = """You are performing a SECURITY AUDIT of a Kotlin Multiplatform (Compose
Multiplatform) app before it is published to TestFlight/Play Store. This is AI-generated code that
has not been manually reviewed.

The following issues were ALREADY detected programmatically — do NOT repeat them:
{checks_summary}

Your task is to review the source code for SECURITY VULNERABILITIES that automated checks might miss:

1. **Data Exposure** — Sensitive data logged, stored in plain text, or sent unencrypted.
   Look for: passwords in SharedPreferences/UserDefaults without encryption, logging PII,
   tokens stored in plain text files.

2. **Injection Risks** — Beyond SQL: command injection, path traversal, unsafe deserialization.
   Look for: Runtime.exec with user input, file paths from user input without sanitization,
   JSON/XML parsing of untrusted data without validation.

3. **Authentication/Authorization** — Weak or missing auth checks.
   Look for: hardcoded passwords, client-side-only auth checks, missing token validation,
   biometric auth that can be bypassed.

4. **Insecure Data Storage** — Sensitive data stored insecurely.
   Look for: API keys in BuildConfig, credentials in resource files, sensitive data in
   app cache/temp files.

Output a JSON object (no markdown fences, just raw JSON):
{{
  "security_score": "pass or warn or fail",
  "findings": [
    {{
      "severity": "critical or warning or info",
      "title": "Short title",
      "detail": "What's wrong and where (include file path)",
      "fix_hint": "How to fix it"
    }}
  ]
}}

Rules:
- ONLY flag real security issues you can see in the code — no hypotheticals
- Reference specific files and code patterns
- Be practical: a simple app with no user auth doesn't need auth checks
- "pass" means no security concerns found
- Output ONLY the JSON object, no other text
"""


def _format_checks_summary(findings: list[SecurityFinding]) -> str:
    """Summarize deterministic findings for the Claude prompt."""
    lines = []
    for f in findings:
        lines.append(f"- [{f.severity.upper()}] {f.title}")
    if not lines:
        lines.append("- All automated security checks passed.")
    return "\n".join(lines)


def _parse_audit_json(raw: str) -> Optional[dict]:
    """Extract and parse JSON from Claude's response."""
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ── Build result dict ──────────────────────────────────────────────────────

def _build_scan_result(
    det_findings: list[SecurityFinding],
    claude_findings: list[dict],
    scan_time: float,
) -> SecurityScanResult:
    """Combine deterministic and Claude findings into SecurityScanResult."""
    has_critical = any(f.severity == "critical" for f in det_findings)
    if not has_critical:
        has_critical = any(
            cf.get("severity") == "critical" for cf in claude_findings
        )

    return SecurityScanResult(
        findings=det_findings,
        claude_findings=claude_findings,
        scan_time_s=scan_time,
        has_critical=has_critical,
    )


# ── Main entry point ──────────────────────────────────────────────────────

async def run_security_scan(
    claude: AgentRunner,
    ws_key: str,
    ws_path: str,
) -> SecurityScanResult:
    """Run full security scan: deterministic checks + Claude self-audit.

    Returns SecurityScanResult with findings and block recommendation.
    """
    start = time.monotonic()

    # Phase 1: Deterministic security checks
    det_findings = run_security_checks(ws_path)
    log.info(
        "security_scan deterministic phase: ws=%s findings=%d critical=%d",
        ws_key, len(det_findings),
        sum(1 for f in det_findings if f.severity == "critical"),
    )

    # Phase 2: Claude self-audit
    claude_findings: list[dict] = []
    try:
        prompt = SECURITY_AUDIT_PROMPT.format(
            checks_summary=_format_checks_summary(det_findings),
        )
        result = await claude.run(prompt, ws_key, ws_path)
        if result.exit_code == 0:
            parsed = _parse_audit_json(result.stdout)
            if parsed:
                claude_findings = parsed.get("findings", [])
    except Exception:
        log.exception("security_scan Claude audit failed for ws=%s", ws_key)

    scan_time = time.monotonic() - start
    scan_result = _build_scan_result(det_findings, claude_findings, scan_time)

    # Audit trail logging
    log.info(
        "security_scan complete: ws=%s time=%.1fs det_findings=%d "
        "claude_findings=%d critical=%s should_block=%s",
        ws_key, scan_time, len(det_findings), len(claude_findings),
        scan_result.has_critical, scan_result.should_block,
    )

    return scan_result
