#!/usr/bin/env python3
"""
GAV - Git Accessibility Validator
Orchestrator | WCAG 2.1 + DITA Accessibility Enforcement

Diff scope  : git diff --name-only origin/main...HEAD
Engines     :
  HTML     ->  axe-core/cli  (WCAG 2.1 AA)
  Markdown ->  markdownlint-cli
  DITA/XML ->  custom lxml parser

Exit codes  :
  0  - all checks passed  (merge allowed)
  1  - violations found   (merge BLOCKED)
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree


# ── Colours (disabled when not in a terminal) ────────────────────────────────

def _c(code: str) -> str:
    return code if sys.stdout.isatty() else ""

RESET  = _c("\033[0m")
RED    = _c("\033[31m")
GREEN  = _c("\033[32m")
YELLOW = _c("\033[33m")
CYAN   = _c("\033[36m")
BOLD   = _c("\033[1m")


def banner(text: str) -> str:
    line = "-" * 70
    return f"\n{CYAN}{BOLD}{line}\n  {text}\n{line}{RESET}"

def ok(msg: str)   -> str: return f"  {GREEN}PASS{RESET}  {msg}"
def err(msg: str)  -> str: return f"  {RED}FAIL{RESET}  {msg}"
def warn(msg: str) -> str: return f"  {YELLOW}SKIP{RESET}  {msg}"
def info(msg: str) -> str: return f"  {CYAN}INFO{RESET}  {msg}"


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Violation:
    file:    str
    line:    int | None
    rule:    str
    message: str
    engine:  str

    def __str__(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return (
            f"\n    {RED}File   :{RESET} {self.file}{loc}"
            f"\n    {YELLOW}Engine :{RESET} {self.engine}"
            f"\n    {BOLD}Rule   :{RESET} {self.rule}"
            f"\n    {BOLD}Issue  :{RESET} {self.message}"
        )


@dataclass
class ValidationResult:
    engine:        str
    files_checked: list[str]       = field(default_factory=list)
    violations:    list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


# ── 1. Git diff ──────────────────────────────────────────────────────────────

def get_changed_files() -> list[Path]:
    """Return all existing files changed between this branch and origin/main."""
    cmd = [
        "git", "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        "origin/main...HEAD"
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        print(err("git diff failed - are you on a feature branch?"))
        print(f"    {exc.stderr.strip()}")
        sys.exit(1)

    paths: list[Path] = []
    for line in result.stdout.splitlines():
        p = Path(line.strip())
        if p.exists():
            paths.append(p)
    return paths


def partition_files(files: list[Path]) -> dict[str, list[Path]]:
    """Group changed files by validation engine."""
    groups: dict[str, list[Path]] = {
        "html":     [],
        "markdown": [],
        "dita":     [],
    }
    for f in files:
        suffix = f.suffix.lower()
        if suffix in {".html", ".htm"}:
            groups["html"].append(f)
        elif suffix in {".md", ".markdown"}:
            groups["markdown"].append(f)
        elif suffix in {".dita", ".ditamap", ".xml"}:
            groups["dita"].append(f)
    return groups


# ── 2. HTML engine - axe-core/cli ────────────────────────────────────────────

def validate_html(files: list[Path]) -> ValidationResult:
    result = ValidationResult(engine="axe-core/cli")
    if not files:
        return result

    for html_file in files:
        result.files_checked.append(str(html_file))
        cmd = ["npx", "axe", str(html_file), "--stdout", "--exit"]
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode == 0:
            continue

        try:
            payload = json.loads(proc.stdout)
            for url_result in payload.get("results", []):
                for violation in url_result.get("violations", []):
                    impact  = violation.get("impact", "unknown").upper()
                    rule_id = violation.get("id", "unknown-rule")
                    desc    = violation.get("description", "")
                    for node in violation.get("nodes", []):
                        target = ", ".join(str(t) for t in node.get("target", []))
                        result.violations.append(Violation(
                            file    = str(html_file),
                            line    = None,
                            rule    = f"[{impact}] {rule_id}",
                            message = f"{desc} - selector: {target}",
                            engine  = "axe-core",
                        ))
        except (json.JSONDecodeError, KeyError):
            raw = (proc.stdout + proc.stderr).strip()
            result.violations.append(Violation(
                file    = str(html_file),
                line    = None,
                rule    = "axe-parse-error",
                message = textwrap.shorten(raw, width=300),
                engine  = "axe-core",
            ))

    return result


# ── 3. Markdown engine - markdownlint-cli ────────────────────────────────────

def validate_markdown(files: list[Path]) -> ValidationResult:
    result = ValidationResult(engine="markdownlint-cli")
    if not files:
        return result

    file_args = [str(f) for f in files]
    result.files_checked.extend(file_args)

    proc = subprocess.run(
        ["npx", "markdownlint", "--json"] + file_args,
        capture_output=True, text=True
    )

    raw = proc.stdout.strip() or proc.stderr.strip()
    if not raw or proc.returncode == 0:
        return result

    try:
        data = json.loads(raw)
        for filepath, findings in data.items():
            for finding in findings:
                line_no = finding.get("lineNumber")
                rule    = " / ".join(finding.get("ruleNames", ["unknown"]))
                desc    = finding.get("ruleDescription", "")
                detail  = finding.get("errorDetail") or ""
                message = f"{desc}{' - ' + detail if detail else ''}"
                result.violations.append(Violation(
                    file    = filepath,
                    line    = line_no,
                    rule    = rule,
                    message = message,
                    engine  = "markdownlint",
                ))
    except (json.JSONDecodeError, TypeError):
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts     = line.split("  ", 2)
            loc_part  = parts[0] if len(parts) > 0 else line
            rule_part = parts[1] if len(parts) > 1 else "unknown"
            msg_part  = parts[2] if len(parts) > 2 else ""
            if ":" in loc_part:
                file_bit, _, ln_bit = loc_part.rpartition(":")
                try:
                    line_no_int: int | None = int(ln_bit)
                except ValueError:
                    file_bit    = loc_part
                    line_no_int = None
            else:
                file_bit    = loc_part
                line_no_int = None
            result.violations.append(Violation(
                file    = file_bit,
                line    = line_no_int,
                rule    = rule_part.strip(),
                message = msg_part.strip(),
                engine  = "markdownlint",
            ))

    return result


# ── 4. DITA engine - custom lxml XML parser ──────────────────────────────────
#
#  Rules enforced:
#
#  DITA-A11Y-001  Every <image> MUST have an <alt>   child  (WCAG 1.1.1)
#  DITA-A11Y-002  Every <table> MUST have a  <thead> child  (WCAG 1.3.1)
#  DITA-A11Y-003  Every <fig>   MUST have a  <title> child  (WCAG 1.1.1)
#
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DitaRule:
    tag:            str
    required_child: str
    rule_id:        str
    rationale:      str


DITA_RULES: list[DitaRule] = [
    DitaRule(
        tag            = "image",
        required_child = "alt",
        rule_id        = "DITA-A11Y-001",
        rationale      = (
            "<image> is missing an <alt> child. "
            "All images must have a text alternative for screen readers. "
            "(WCAG 1.1.1)"
        ),
    ),
    DitaRule(
        tag            = "table",
        required_child = "thead",
        rule_id        = "DITA-A11Y-002",
        rationale      = (
            "<table> is missing a <thead> child. "
            "Table headers are required for assistive technology. "
            "(WCAG 1.3.1)"
        ),
    ),
    DitaRule(
        tag            = "fig",
        required_child = "title",
        rule_id        = "DITA-A11Y-003",
        rationale      = (
            "<fig> is missing a <title> child. "
            "Figures must have captions for non-visual users. "
            "(WCAG 1.1.1)"
        ),
    ),
]


def _strip_ns(tag: str) -> str:
    """Strip Clark-notation namespace from tag name."""
    return tag.split("}")[-1] if "}" in tag else tag


def _apply_dita_rules(tree: etree._ElementTree, filepath: str) -> list[Violation]:
    violations: list[Violation] = []

    for element in tree.iter():
        local_tag = _strip_ns(element.tag)

        for rule in DITA_RULES:
            if local_tag != rule.tag:
                continue

            child_tags = {
                _strip_ns(child.tag)
                for child in element
                if isinstance(child.tag, str)
            }

            if rule.required_child not in child_tags:
                violations.append(Violation(
                    file    = filepath,
                    line    = getattr(element, "sourceline", None),
                    rule    = rule.rule_id,
                    message = rule.rationale,
                    engine  = "dita-lxml",
                ))

    return violations


def validate_dita(files: list[Path]) -> ValidationResult:
    result = ValidationResult(engine="dita-lxml")
    if not files:
        return result

    parser = etree.XMLParser(
        recover          = True,
        resolve_entities = False,
        load_dtd         = False,
    )

    for xml_file in files:
        result.files_checked.append(str(xml_file))
        try:
            tree = etree.parse(str(xml_file), parser)
        except etree.XMLSyntaxError as exc:
            result.violations.append(Violation(
                file    = str(xml_file),
                line    = exc.lineno,
                rule    = "DITA-PARSE-ERROR",
                message = f"XML is not well-formed: {exc.msg}",
                engine  = "dita-lxml",
            ))
            continue

        result.violations.extend(_apply_dita_rules(tree, str(xml_file)))

    return result


# ── 5. Reporting ─────────────────────────────────────────────────────────────

def print_engine_result(res: ValidationResult) -> None:
    total   = len(res.files_checked)
    n_viols = len(res.violations)

    if total == 0:
        print(warn(f"{res.engine}: no files in scope - skipped"))
        return

    label = f"{res.engine}  ({total} file{'s' if total != 1 else ''} checked)"

    if res.passed:
        print(ok(label))
    else:
        print(err(f"{label}  ->  {n_viols} violation{'s' if n_viols != 1 else ''} found"))
        for v in res.violations:
            print(v)
        print()


def print_summary(results: list[ValidationResult]) -> bool:
    total_violations = sum(len(r.violations) for r in results)
    total_files      = sum(len(r.files_checked) for r in results)
    engines_used     = [r.engine for r in results if r.files_checked]

    print(banner("GAV GATE SUMMARY"))
    print(info(f"Files checked : {total_files}"))
    print(info(f"Engines run   : {', '.join(engines_used) if engines_used else 'none'}"))

    if total_violations == 0:
        print(f"\n  {GREEN}{BOLD}GATE PASSED - No accessibility violations detected.{RESET}")
        print(f"  {GREEN}Merge is allowed.{RESET}\n")
        return True
    else:
        blocks = sum(1 for r in results if r.violations)
        print(f"\n  {RED}{BOLD}GATE BLOCKED - {total_violations} violation{'s' if total_violations != 1 else ''} found across {blocks} engine{'s' if blocks != 1 else ''}.{RESET}")
        print(f"  {RED}Resolve all violations before retrying.{RESET}\n")
        return False


# ── 6. Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print(banner("GAV - Git Accessibility Validator"))
    print(info("WCAG 2.1 enforcement on PR diff\n"))

    print(banner("STEP 1 - Resolving changed files"))
    changed = get_changed_files()

    if not changed:
        print(ok("No changed files detected - gate passes by default."))
        sys.exit(0)

    groups = partition_files(changed)

    for engine_name, paths in groups.items():
        if paths:
            for p in paths:
                print(info(f"[{engine_name.upper():8}] {p}"))
        else:
            print(warn(f"[{engine_name.upper():8}] no files in scope"))

    print(banner("STEP 2 - Running validation engines"))

    results: list[ValidationResult] = [
        validate_html(groups["html"]),
        validate_markdown(groups["markdown"]),
        validate_dita(groups["dita"]),
    ]

    for res in results:
        print_engine_result(res)

    passed = print_summary(results)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()