#!/usr/bin/env python3
"""Gap auditor: check whether bottest contains curated feature fingerprints from WereSoBach.

Reads patterns from a YAML file, globs files in the bottest checkout, searches for marker
substrings, and emits findings as JSON + a human-readable markdown report. Used by the
Path B pipeline: when bottest fails a pattern, the GH workflow opens a draft PR on
discord-claude-app-builder (this repo) appending the finding to gap-audit-log.md.

Usage:
    python3 scripts/gap_auditor.py \\
        --bottest-dir /path/to/weresobachbottest \\
        --weresobach-dir /path/to/WereSoBach \\
        --patterns gap-audit-patterns.yaml \\
        --out-json gaps.json \\
        --out-md gaps.md
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class PatternResult:
    name: str
    description: str
    status: str  # "pass" | "fail" | "error"
    matched_markers: list[str] = field(default_factory=list)
    missing_markers: list[str] = field(default_factory=list)
    matched_files: list[str] = field(default_factory=list)
    exemplars_found: list[str] = field(default_factory=list)
    exemplars_missing: list[str] = field(default_factory=list)
    prompt_fix: str = ""
    error: str | None = None


def load_patterns(path: Path) -> list[dict]:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "patterns" not in data:
        raise ValueError(f"{path} missing top-level 'patterns' list")
    return data["patterns"]


def scan_pattern(pattern: dict, bottest_dir: Path, weresobach_dir: Path) -> PatternResult:
    name = pattern.get("name", "<unnamed>")
    result = PatternResult(
        name=name,
        description=pattern.get("description", ""),
        status="error",
        prompt_fix=pattern.get("prompt_fix", "").strip(),
    )

    markers = pattern.get("markers") or []
    if not markers:
        result.error = "pattern has no markers"
        return result

    mode = pattern.get("mode", "any")
    if mode not in ("any", "all"):
        result.error = f"invalid mode: {mode!r}"
        return result

    globs = pattern.get("file_globs") or ["**/*"]

    candidate_files: set[Path] = set()
    for glob in globs:
        candidate_files.update(bottest_dir.glob(glob))

    marker_hits: dict[str, list[Path]] = {m: [] for m in markers}
    for file_path in sorted(candidate_files):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(errors="replace")
        except OSError:
            continue
        for marker in markers:
            if marker in text:
                marker_hits[marker].append(file_path)

    matched = [m for m, hits in marker_hits.items() if hits]
    missing = [m for m, hits in marker_hits.items() if not hits]
    matched_files_set: set[Path] = set()
    for hits in marker_hits.values():
        matched_files_set.update(hits)

    if mode == "any":
        passed = len(matched) > 0
    else:
        passed = len(missing) == 0

    result.status = "pass" if passed else "fail"
    result.matched_markers = matched
    result.missing_markers = missing
    result.matched_files = sorted(
        str(p.relative_to(bottest_dir)) for p in matched_files_set
    )

    for exemplar in pattern.get("weresobach_exemplars", []):
        exemplar_path = weresobach_dir / exemplar
        if exemplar_path.is_file():
            result.exemplars_found.append(exemplar)
        else:
            result.exemplars_missing.append(exemplar)

    return result


def render_markdown(results: list[PatternResult], bottest_sha: str | None) -> str:
    passed = [r for r in results if r.status == "pass"]
    failed = [r for r in results if r.status == "fail"]
    errored = [r for r in results if r.status == "error"]

    lines: list[str] = []
    lines.append("# Gap audit report")
    lines.append("")
    if bottest_sha:
        lines.append(f"**bottest commit:** `{bottest_sha}`")
        lines.append("")
    lines.append(
        f"**Summary:** {len(passed)} pass · {len(failed)} fail"
        + (f" · {len(errored)} error" if errored else "")
        + f" · {len(results)} total"
    )
    lines.append("")

    if failed:
        lines.append("## Failing patterns")
        lines.append("")
        for r in failed:
            lines.append(f"### {r.name} — {r.description}")
            lines.append("")
            lines.append(f"- **Missing markers:** `{', '.join(r.missing_markers) or '—'}`")
            if r.matched_markers:
                lines.append(f"- **Partial (found):** `{', '.join(r.matched_markers)}`")
            if r.exemplars_found:
                lines.append("- **WereSoBach exemplars:**")
                for ex in r.exemplars_found:
                    lines.append(f"  - `{ex}`")
            if r.exemplars_missing:
                lines.append(
                    f"- **Stale exemplars (not in WereSoBach):** `{', '.join(r.exemplars_missing)}`"
                )
            lines.append("")
            lines.append("**Prompt fix suggestion:**")
            lines.append("")
            lines.append("> " + r.prompt_fix.replace("\n", "\n> "))
            lines.append("")

    if errored:
        lines.append("## Errored patterns")
        lines.append("")
        for r in errored:
            lines.append(f"- **{r.name}:** {r.error}")
        lines.append("")

    if passed:
        lines.append("## Passing patterns")
        lines.append("")
        for r in passed:
            files = (
                f" (in `{r.matched_files[0]}`"
                + (f" +{len(r.matched_files) - 1} more" if len(r.matched_files) > 1 else "")
                + ")"
                if r.matched_files
                else ""
            )
            lines.append(f"- `{r.name}`{files}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bottest-dir", required=True, type=Path)
    ap.add_argument("--weresobach-dir", required=True, type=Path)
    ap.add_argument("--patterns", required=True, type=Path)
    ap.add_argument("--out-json", type=Path)
    ap.add_argument("--out-md", type=Path)
    ap.add_argument("--bottest-sha", default=None, help="optional SHA to include in report")
    ap.add_argument(
        "--fail-on-gap",
        action="store_true",
        help="exit non-zero if any pattern fails (default: always exit 0)",
    )
    args = ap.parse_args()

    for label, path in (
        ("bottest-dir", args.bottest_dir),
        ("weresobach-dir", args.weresobach_dir),
        ("patterns", args.patterns),
    ):
        if not path.exists():
            print(f"error: --{label} not found: {path}", file=sys.stderr)
            return 2

    patterns = load_patterns(args.patterns)
    results = [scan_pattern(p, args.bottest_dir, args.weresobach_dir) for p in patterns]

    payload = {
        "bottest_sha": args.bottest_sha,
        "totals": {
            "pass": sum(1 for r in results if r.status == "pass"),
            "fail": sum(1 for r in results if r.status == "fail"),
            "error": sum(1 for r in results if r.status == "error"),
            "total": len(results),
        },
        "results": [asdict(r) for r in results],
    }

    if args.out_json:
        args.out_json.write_text(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload, indent=2))

    if args.out_md:
        args.out_md.write_text(render_markdown(results, args.bottest_sha))

    any_fail = any(r.status == "fail" for r in results)
    if args.fail_on_gap and any_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
