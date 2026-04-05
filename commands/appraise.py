"""
commands/appraise.py — Deterministic + AI app appraisal against store guidelines.

Two-tier system:
1. Deterministic file checks (Python) — fast, auto-fixes safe issues
2. Claude deep scan — evaluates functional completeness (the #1 rejection reason)

Used as quality gate before /testflight and /playstore, and as standalone /appraise.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent_protocol import AgentRunner


# ── Constants ───────────────────────────────────────────────────────────────

MIN_TARGET_SDK = 35  # Google Play requirement (Aug 2025)

PLACEHOLDER_NAMES = {
    "kmptemplate", "myapp", "my app", "template", "untitled",
    "example", "test app", "sample app", "hello world", "new app",
}

TEMPLATE_PACKAGES = {
    "com.example", "org.example", "com.jetbrains",
    "com.jaredtan.kmptemplate",
}

SECRET_PATTERNS = [
    (r'AIza[0-9A-Za-z\-_]{35}', "Google API key"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
    (r'"sk-[a-zA-Z0-9]{20,}"', "OpenAI API key"),
    (r'"ghp_[a-zA-Z0-9]{36}"', "GitHub token"),
    (r'(?:api_key|secret_key|api_secret|password)\s*=\s*"[^"]{10,}"',
     "Hardcoded secret"),
]

NETWORKING_INDICATORS = [
    "HttpClient", "Supabase", "supabase", "URLSession",
    "OkHttp", "Retrofit", "WebSocket", "io.ktor", "fetch(",
]


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class Finding:
    check_id: str
    severity: str       # critical, warning, info
    title: str
    detail: str
    fix_hint: str
    platform: str       # apple, google, both
    auto_fixed: bool = False
    fix_description: str = ""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _scan_source_files(root: Path) -> list[tuple[Path, str]]:
    """Read source files from workspace. Returns (path, content) pairs."""
    files = []
    for src_dir in ["composeApp/src", "iosApp/iosApp"]:
        src_path = root / src_dir
        if not src_path.exists():
            continue
        for fp in src_path.rglob("*"):
            if fp.suffix not in (".kt", ".swift"):
                continue
            if any(s in fp.parts for s in ("build", ".gradle", "test", "Test", "androidTest")):
                continue
            if fp.stat().st_size > 100_000:  # skip large generated files
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


def _has_networking(source_files: list[tuple[Path, str]]) -> bool:
    """Check if any source file references networking APIs."""
    for _, content in source_files:
        for indicator in NETWORKING_INDICATORS:
            if indicator in content:
                return True
    return False


# ── Config file checks (with auto-fixes) ───────────────────────────────────

def _check_encryption_declaration(root: Path) -> list[Finding]:
    """Apple: ITSAppUsesNonExemptEncryption must be in Info.plist."""
    plist = root / "iosApp" / "iosApp" / "Info.plist"
    text = _read_if_exists(plist)
    if text is None:
        return []  # no iOS target

    if "ITSAppUsesNonExemptEncryption" in text:
        return []

    # Auto-fix: add the key (false = standard HTTPS only, which KMP apps use)
    new_text = text.replace(
        "</dict>",
        "\t<key>ITSAppUsesNonExemptEncryption</key>\n\t<false/>\n</dict>",
    )
    plist.write_text(new_text)

    return [Finding(
        check_id="encryption_declaration",
        severity="critical",
        title="Missing encryption declaration",
        detail="Info.plist lacked ITSAppUsesNonExemptEncryption — "
               "without this, every TestFlight build triggers a compliance hold.",
        fix_hint="Added ITSAppUsesNonExemptEncryption=false to Info.plist",
        platform="apple",
        auto_fixed=True,
        fix_description="Added <key>ITSAppUsesNonExemptEncryption</key><false/> to Info.plist",
    )]


def _check_internet_permission(root: Path, has_net: bool) -> list[Finding]:
    """Android: INTERNET permission needed if app uses networking."""
    manifest = root / "composeApp" / "src" / "androidMain" / "AndroidManifest.xml"
    text = _read_if_exists(manifest)
    if text is None:
        return []

    if "android.permission.INTERNET" in text:
        return []
    if not has_net:
        return []  # app doesn't use networking, permission not needed

    # Auto-fix: add INTERNET permission
    new_text = text.replace(
        "<application",
        '    <uses-permission android:name="android.permission.INTERNET" />\n\n    <application',
    )
    manifest.write_text(new_text)

    return [Finding(
        check_id="internet_permission",
        severity="critical",
        title="Missing INTERNET permission",
        detail="App uses networking APIs but AndroidManifest.xml lacks INTERNET permission — "
               "all network calls would silently fail.",
        fix_hint="Added INTERNET permission to AndroidManifest.xml",
        platform="google",
        auto_fixed=True,
        fix_description="Added <uses-permission android:name=\"android.permission.INTERNET\" /> "
                        "to AndroidManifest.xml",
    )]


def _check_target_sdk(root: Path) -> list[Finding]:
    """Android: targetSdk must be >= MIN_TARGET_SDK for Google Play."""
    # Check libs.versions.toml first (KMP convention)
    toml = root / "gradle" / "libs.versions.toml"
    text = _read_if_exists(toml)
    if text:
        m = re.search(r'android-targetSdk\s*=\s*"(\d+)"', text)
        if m and int(m.group(1)) < MIN_TARGET_SDK:
            old_val = int(m.group(1))
            new_text = text.replace(m.group(0), f'android-targetSdk = "{MIN_TARGET_SDK}"')
            toml.write_text(new_text)
            return [Finding(
                check_id="target_sdk",
                severity="critical",
                title=f"targetSdk too low ({old_val})",
                detail=f"Google Play requires targetSdk >= {MIN_TARGET_SDK} for new apps "
                       f"and updates. Was set to {old_val}.",
                fix_hint=f"Updated to targetSdk {MIN_TARGET_SDK}",
                platform="google",
                auto_fixed=True,
                fix_description=f"Updated android-targetSdk from {old_val} to {MIN_TARGET_SDK} "
                                f"in libs.versions.toml",
            )]
        return []

    # Fallback: check build.gradle.kts directly
    gradle = root / "composeApp" / "build.gradle.kts"
    text = _read_if_exists(gradle)
    if text:
        m = re.search(r'targetSdk\s*=\s*(\d+)', text)
        if m and int(m.group(1)) < MIN_TARGET_SDK:
            old_val = int(m.group(1))
            new_text = text.replace(m.group(0), f"targetSdk = {MIN_TARGET_SDK}")
            gradle.write_text(new_text)
            return [Finding(
                check_id="target_sdk",
                severity="critical",
                title=f"targetSdk too low ({old_val})",
                detail=f"Google Play requires targetSdk >= {MIN_TARGET_SDK}. Was {old_val}.",
                fix_hint=f"Updated to targetSdk {MIN_TARGET_SDK}",
                platform="google",
                auto_fixed=True,
                fix_description=f"Updated targetSdk from {old_val} to {MIN_TARGET_SDK} "
                                f"in build.gradle.kts",
            )]

    return []


# ── Config file checks (warn only) ─────────────────────────────────────────

def _check_app_name(root: Path) -> list[Finding]:
    """Check if app name is still a placeholder."""
    names_found = []

    # strings.xml
    strings = root / "composeApp" / "src" / "androidMain" / "res" / "values" / "strings.xml"
    text = _read_if_exists(strings)
    if text:
        m = re.search(r'<string name="app_name">([^<]+)</string>', text)
        if m:
            names_found.append(("strings.xml", m.group(1)))

    # Config.xcconfig
    xcconfig = root / "iosApp" / "Configuration" / "Config.xcconfig"
    text = _read_if_exists(xcconfig)
    if text:
        m = re.search(r'PRODUCT_NAME\s*=\s*(.+)', text)
        if m:
            names_found.append(("Config.xcconfig", m.group(1).strip()))

    # settings.gradle.kts
    settings = root / "settings.gradle.kts"
    text = _read_if_exists(settings)
    if text:
        m = re.search(r'rootProject\.name\s*=\s*"([^"]+)"', text)
        if m:
            names_found.append(("settings.gradle.kts", m.group(1)))

    for fname, name in names_found:
        if name.lower().replace(" ", "").replace("-", "").replace("_", "") in PLACEHOLDER_NAMES:
            return [Finding(
                check_id="placeholder_name",
                severity="critical",
                title=f'App name is still "{name}"',
                detail=f'Found placeholder name "{name}" in {fname}. '
                       "Apple and Google reject apps with generic/template names.",
                fix_hint="Use /appname YourRealAppName to rename the app everywhere.",
                platform="both",
            )]

    return []


def _check_package_name(root: Path) -> list[Finding]:
    """Check if package/bundle ID is still the template default."""
    gradle = root / "composeApp" / "build.gradle.kts"
    text = _read_if_exists(gradle)
    if text:
        m = re.search(r'applicationId\s*=\s*"([^"]+)"', text)
        if m:
            app_id = m.group(1)
            for tmpl in TEMPLATE_PACKAGES:
                if app_id.startswith(tmpl):
                    return [Finding(
                        check_id="template_package",
                        severity="critical",
                        title=f'Package name is template default',
                        detail=f'applicationId is "{app_id}" — still using the template '
                               "package name. Both stores reject template/example IDs.",
                        fix_hint="Change applicationId in composeApp/build.gradle.kts to your "
                                 "own reverse-domain (e.g. com.yourcompany.yourapp).",
                        platform="both",
                    )]

    xcconfig = root / "iosApp" / "Configuration" / "Config.xcconfig"
    text = _read_if_exists(xcconfig)
    if text:
        m = re.search(r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*(.+)', text)
        if m:
            bundle_id = m.group(1).strip()
            if "kmptemplate" in bundle_id.lower() or "example" in bundle_id.lower():
                return [Finding(
                    check_id="template_bundle_id",
                    severity="critical",
                    title="Bundle ID is template default",
                    detail=f'PRODUCT_BUNDLE_IDENTIFIER is "{bundle_id}" — '
                           "still using the template bundle ID.",
                    fix_hint="Update PRODUCT_BUNDLE_IDENTIFIER in Config.xcconfig.",
                    platform="apple",
                )]

    return []


def _check_app_icon(root: Path) -> list[Finding]:
    """Check if a real app icon exists."""
    icon_dir = root / "iosApp" / "iosApp" / "Assets.xcassets" / "AppIcon.appiconset"
    if not icon_dir.exists():
        return []

    # Check for any PNG file > 1KB (not an empty placeholder)
    has_icon = False
    for png in icon_dir.glob("*.png"):
        if png.stat().st_size > 1024:
            has_icon = True
            break

    if not has_icon:
        return [Finding(
            check_id="app_icon_missing",
            severity="warning",
            title="App icon may be missing or placeholder",
            detail="No substantial PNG found in AppIcon.appiconset. "
                   "Apple requires a 1024x1024 app icon (ITMS-90704).",
            fix_hint="Add a 1024x1024 PNG named app-icon-1024.png to "
                     "iosApp/iosApp/Assets.xcassets/AppIcon.appiconset/",
            platform="apple",
        )]
    return []


def _check_cleartext_traffic(root: Path) -> list[Finding]:
    """Android: usesCleartextTraffic should be false for production."""
    manifest = root / "composeApp" / "src" / "androidMain" / "AndroidManifest.xml"
    text = _read_if_exists(manifest)
    if text and 'usesCleartextTraffic="true"' in text:
        return [Finding(
            check_id="cleartext_traffic",
            severity="warning",
            title="Cleartext HTTP traffic enabled",
            detail='AndroidManifest has usesCleartextTraffic="true" — '
                   "Google flags this as a security issue.",
            fix_hint='Remove android:usesCleartextTraffic="true" from AndroidManifest.xml '
                     "or set it to false.",
            platform="google",
        )]
    return []


def _check_ats_bypass(root: Path) -> list[Finding]:
    """Apple: NSAllowsArbitraryLoads disables App Transport Security."""
    plist = root / "iosApp" / "iosApp" / "Info.plist"
    text = _read_if_exists(plist)
    if text and "NSAllowsArbitraryLoads" in text and "<true/>" in text:
        return [Finding(
            check_id="ats_bypass",
            severity="warning",
            title="App Transport Security bypassed",
            detail="Info.plist has NSAllowsArbitraryLoads=true — this disables HTTPS "
                   "enforcement. Apple requires justification during review.",
            fix_hint="Remove the NSAllowsArbitraryLoads exception, or add per-domain "
                     "exceptions for specific HTTP hosts that require it.",
            platform="apple",
        )]
    return []


# ── Source code checks ──────────────────────────────────────────────────────

def _check_source_issues(root: Path, source_files: list[tuple[Path, str]]) -> list[Finding]:
    """Scan source files for secrets, TODOs, placeholder content, debug logging."""
    findings = []
    secret_hits: list[tuple[str, str]] = []  # (relative_path, label)
    todo_count = 0
    todo_files: set[str] = set()
    placeholder_files: list[str] = []
    debug_files: set[str] = set()
    http_files: list[tuple[str, str]] = []  # (relative_path, url)
    localhost_files: set[str] = set()

    for fp, content in source_files:
        rel = str(fp.relative_to(root))

        # Hardcoded secrets
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                secret_hits.append((rel, label))
                break  # one hit per file is enough

        # TODOs / FIXMEs
        matches = re.findall(r'\b(?:TODO|FIXME|HACK|XXX)\b', content)
        if matches:
            todo_count += len(matches)
            todo_files.add(rel)

        # Placeholder/lorem ipsum
        for pat in [r'lorem\s+ipsum', r'dolor\s+sit\s+amet',
                    r'"placeholder"', r'"sample\s+text"']:
            if re.search(pat, content, re.IGNORECASE):
                placeholder_files.append(rel)
                break

        # Debug logging
        if re.search(r'\bprintln\s*\(', content) or re.search(r'\bLog\.[dvwe]\s*\(', content):
            debug_files.add(rel)

        # HTTP URLs (not localhost, not comments)
        for m in re.finditer(r'http://(?!localhost|127\.0\.0\.1|10\.0\.)[^\s"\'<>]+', content):
            http_files.append((rel, m.group(0)[:60]))

        # Localhost/dev URLs
        if re.search(r'(?:localhost|127\.0\.0\.1|10\.0\.2\.2):\d+', content):
            localhost_files.add(rel)

    # Build findings from collected data
    if secret_hits:
        files_str = ", ".join(f"**{f}** ({label})" for f, label in secret_hits[:5])
        findings.append(Finding(
            check_id="hardcoded_secrets",
            severity="critical",
            title=f"Hardcoded secrets in {len(secret_hits)} file(s)",
            detail=f"Potential secrets found: {files_str}. "
                   "Exposed API keys are a security risk and can cause store removal.",
            fix_hint="Move secrets to local.properties or environment variables. "
                     "Never commit API keys to source code.",
            platform="both",
        ))

    if todo_count > 5:
        findings.append(Finding(
            check_id="todo_comments",
            severity="warning",
            title=f"{todo_count} TODO/FIXME comments across {len(todo_files)} files",
            detail="Large number of incomplete markers suggests the app isn't finished. "
                   "Apple's #1 rejection reason is 'App Completeness' (Guideline 2.1).",
            fix_hint="Complete or remove TODO items before publishing.",
            platform="both",
        ))
    elif todo_count > 0:
        findings.append(Finding(
            check_id="todo_comments",
            severity="info",
            title=f"{todo_count} TODO/FIXME comment(s)",
            detail=f"Found in: {', '.join(list(todo_files)[:3])}",
            fix_hint="Review and resolve before publishing.",
            platform="both",
        ))

    if placeholder_files:
        findings.append(Finding(
            check_id="placeholder_content",
            severity="critical",
            title=f"Placeholder content in {len(placeholder_files)} file(s)",
            detail=f"Lorem ipsum or placeholder text found in: "
                   f"{', '.join(placeholder_files[:3])}. "
                   "Apps with placeholder content are rejected (Guideline 2.1).",
            fix_hint="Replace all placeholder text with real content.",
            platform="both",
        ))

    if debug_files:
        findings.append(Finding(
            check_id="debug_logging",
            severity="warning",
            title=f"Debug logging in {len(debug_files)} file(s)",
            detail=f"println() or Log.d() found in: {', '.join(list(debug_files)[:3])}. "
                   "Debug logging can leak sensitive data and slow the app.",
            fix_hint="Remove or gate debug logging behind a DEBUG flag.",
            platform="both",
        ))

    if http_files:
        findings.append(Finding(
            check_id="http_urls",
            severity="warning",
            title=f"Non-HTTPS URL(s) in source code",
            detail=f"HTTP (not HTTPS) URLs found in: "
                   f"{', '.join(f[0] for f in http_files[:3])}. "
                   "Both stores require HTTPS for network communication.",
            fix_hint="Change http:// to https:// for all remote URLs.",
            platform="both",
        ))

    if localhost_files:
        findings.append(Finding(
            check_id="localhost_urls",
            severity="warning",
            title=f"Localhost/dev URLs in {len(localhost_files)} file(s)",
            detail=f"Development server URLs found in: "
                   f"{', '.join(list(localhost_files)[:3])}. "
                   "These will fail on real devices.",
            fix_hint="Replace localhost URLs with production API endpoints.",
            platform="both",
        ))

    return findings


# ── Orchestrator ────────────────────────────────────────────────────────────

def run_deterministic_checks(ws_path: str, platform: str = "both") -> list[Finding]:
    """Run all deterministic file checks. Returns list of findings (some auto-fixed)."""
    root = Path(ws_path)
    findings: list[Finding] = []
    source_files = _scan_source_files(root)
    has_net = _has_networking(source_files)

    check_apple = platform in ("apple", "both")
    check_google = platform in ("google", "both")

    # Config checks with auto-fixes
    if check_apple:
        findings.extend(_check_encryption_declaration(root))
    if check_google:
        findings.extend(_check_internet_permission(root, has_net))
        findings.extend(_check_target_sdk(root))

    # Config checks (warn only)
    findings.extend(_check_app_name(root))
    findings.extend(_check_package_name(root))
    if check_apple:
        findings.extend(_check_app_icon(root))
        findings.extend(_check_ats_bypass(root))
    if check_google:
        findings.extend(_check_cleartext_traffic(root))

    # Source code checks
    findings.extend(_check_source_issues(root, source_files))

    return findings


# ── Claude deep scan (functional completeness only) ─────────────────────────

COMPLETENESS_PROMPT = """You are reviewing a Kotlin Multiplatform (Compose Multiplatform) app's
source code for FUNCTIONAL COMPLETENESS — the #1 reason apps get rejected (Apple Guideline 2.1,
accounting for 40%+ of all rejections).

The following technical checks have ALREADY been performed programmatically — do NOT repeat them:
{checks_summary}

Your ONLY task is to evaluate the app's functional completeness by reading the source code:

1. **Screen Completeness** — Are screens implemented with real UI, or are they stubs?
   Look for: screens with only Text("Hello") or placeholder composables, empty click handlers,
   ViewModels returning hardcoded dummy data.

2. **Feature Completeness** — Do interactive elements actually work?
   Look for: buttons with empty/TODO callbacks, navigation that leads nowhere, forms that
   don't submit, lists with hardcoded sample data.

3. **Error Handling** — Does the app handle common failure cases?
   Look for: network calls without try/catch, missing loading states, missing empty states,
   unhandled null cases.

Output a JSON object (no markdown fences, just raw JSON):
{{
  "completeness_score": "pass or warn or fail",
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
- ONLY evaluate functional completeness, not configuration (already checked)
- Reference specific files and composable functions when possible
- Be practical — only flag issues you can actually see in the code
- An app with 2-3 working screens with real UI is a "pass"
- Output ONLY the JSON object, no other text
"""


def _format_checks_summary(findings: list[Finding]) -> str:
    """Summarize deterministic check results for the Claude prompt."""
    lines = []
    for f in findings:
        status = "AUTO-FIXED" if f.auto_fixed else f.severity.upper()
        lines.append(f"- [{status}] {f.title}")
    if not lines:
        lines.append("- All configuration checks passed.")
    return "\n".join(lines)


def parse_appraisal_json(raw: str) -> Optional[dict]:
    """Extract and parse JSON from Claude's response (3-tier fallback)."""
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


# ── Results formatting ──────────────────────────────────────────────────────

def _build_appraisal_dict(
    det_findings: list[Finding],
    claude_findings: list[dict],
    platform: str,
) -> dict:
    """Combine deterministic and Claude findings into the appraisal dict format."""
    auto_fixes = [f for f in det_findings if f.auto_fixed]
    unfixed = [f for f in det_findings if not f.auto_fixed]

    # Build categories
    categories = []

    # Category: Auto-Fixed Issues
    if auto_fixes:
        categories.append({
            "name": "Auto-Fixed",
            "score": "pass",
            "findings": [{
                "severity": "info",
                "title": f.title,
                "detail": f.fix_description,
                "fix_hint": "",
            } for f in auto_fixes],
        })

    # Group unfixed deterministic findings by type
    config_findings = [f for f in unfixed if f.check_id in (
        "placeholder_name", "template_package", "template_bundle_id",
        "app_icon_missing", "cleartext_traffic", "ats_bypass",
    )]
    security_findings = [f for f in unfixed if f.check_id in (
        "hardcoded_secrets", "http_urls", "localhost_urls",
    )]
    code_findings = [f for f in unfixed if f.check_id in (
        "todo_comments", "placeholder_content", "debug_logging",
    )]

    if config_findings:
        worst = "fail" if any(f.severity == "critical" for f in config_findings) else "warn"
        categories.append({
            "name": "App Identity & Config",
            "score": worst,
            "findings": [{
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "fix_hint": f.fix_hint,
            } for f in config_findings],
        })

    if security_findings:
        worst = "fail" if any(f.severity == "critical" for f in security_findings) else "warn"
        categories.append({
            "name": "Security",
            "score": worst,
            "findings": [{
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "fix_hint": f.fix_hint,
            } for f in security_findings],
        })

    if code_findings:
        worst = "fail" if any(f.severity == "critical" for f in code_findings) else "warn"
        categories.append({
            "name": "Code Quality",
            "score": worst,
            "findings": [{
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "fix_hint": f.fix_hint,
            } for f in code_findings],
        })

    # Category: Functional Completeness (from Claude)
    if claude_findings:
        worst_sev = "pass"
        for cf in claude_findings:
            if cf.get("severity") == "critical":
                worst_sev = "fail"
            elif cf.get("severity") == "warning" and worst_sev != "fail":
                worst_sev = "warn"
        categories.append({
            "name": "Functional Completeness",
            "score": worst_sev,
            "findings": claude_findings,
        })

    # Compute overall score
    all_unfixed_severities = [f.severity for f in unfixed]
    for cf in claude_findings:
        all_unfixed_severities.append(cf.get("severity", "info"))

    if any(s == "critical" for s in all_unfixed_severities):
        overall_score = "fail"
    elif any(s == "warning" for s in all_unfixed_severities):
        overall_score = "warn"
    else:
        overall_score = "pass"

    # Build summary
    n_fixed = len(auto_fixes)
    n_critical = sum(1 for s in all_unfixed_severities if s == "critical")
    n_warning = sum(1 for s in all_unfixed_severities if s == "warning")

    summary_parts = []
    if n_fixed:
        summary_parts.append(f"{n_fixed} issue(s) auto-fixed")
    if n_critical:
        summary_parts.append(f"{n_critical} blocking issue(s)")
    if n_warning:
        summary_parts.append(f"{n_warning} warning(s)")
    if not summary_parts:
        summary_parts.append("App looks ready for store submission")

    # Blocking issues list
    blocking = [f.title for f in unfixed if f.severity == "critical"]
    for cf in claude_findings:
        if cf.get("severity") == "critical":
            blocking.append(cf.get("title", ""))

    # Recommendations list
    recommendations = [f.fix_hint for f in unfixed if f.severity == "info" and f.fix_hint]

    return {
        "overall_score": overall_score,
        "overall_summary": ". ".join(summary_parts) + ".",
        "categories": categories,
        "blocking_issues": blocking,
        "recommendations": recommendations,
        "auto_fixes": [{
            "title": f.title,
            "detail": f.fix_description,
        } for f in auto_fixes],
    }


# ── Main entry point ───────────────────────────────────────────────────────

async def run_appraisal(
    claude: AgentRunner,
    ws_key: str,
    ws_path: str,
    platform: str = "both",
) -> Optional[dict]:
    """Run full appraisal: deterministic checks + Claude completeness scan.

    Returns appraisal dict or None on total failure.
    """
    # Phase 1: Deterministic checks (fast, auto-fixes)
    det_findings = run_deterministic_checks(ws_path, platform)

    # Phase 2: Claude completeness scan
    # Skip if too many critical issues already — app clearly isn't ready
    unfixed_critical = sum(
        1 for f in det_findings if f.severity == "critical" and not f.auto_fixed
    )
    claude_findings: list[dict] = []

    if unfixed_critical <= 3:
        platform_label = {
            "apple": "Apple App Store",
            "google": "Google Play Store",
            "both": "Apple App Store and Google Play Store",
        }.get(platform, "both stores")

        prompt = COMPLETENESS_PROMPT.format(
            checks_summary=_format_checks_summary(det_findings),
        )
        result = await claude.run(prompt, ws_key, ws_path)
        if result.exit_code == 0:
            parsed = parse_appraisal_json(result.stdout)
            if parsed:
                claude_findings = parsed.get("findings", [])

    return _build_appraisal_dict(det_findings, claude_findings, platform)


# ── Emoji helpers (used by views) ───────────────────────────────────────────

def score_emoji(score: str) -> str:
    return {"pass": "\u2705", "warn": "\u26a0\ufe0f", "fail": "\u274c"}.get(score, "\u2753")


def score_color(score: str) -> int:
    return {"pass": 0x34C759, "warn": 0xFF9500, "fail": 0xFF3B30}.get(score, 0x8E8E93)


def severity_emoji(severity: str) -> str:
    return {
        "critical": "\U0001f6d1", "warning": "\u26a0\ufe0f", "info": "\U0001f4a1",
    }.get(severity, "\u2022")
