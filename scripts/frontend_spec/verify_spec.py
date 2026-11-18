"""Machine check of frontend spec documents against the code they cite (anti-hallucination gate).

A spec table row that cites code (`path/File.tsx:108` or `File.tsx:124-170`) makes claims. This
verifier checks, without any model, that:
  1. every cited file exists (a bare basename shared by several files is reported as ambiguous)
     and every cited line or range lies inside the file;
  2. every other code-looking identifier quoted in backticks on the same row occurs in the cited
     file (an invented field such as `order_type` on a page that never mentions it is reported);
  3. screen specs contain every section heading of the screen-spec template.

A finding means "the cited code does not contain this" -- a fact about the document, not an
opinion. Legacy documents that predate the gate are listed in a baseline and ratcheted down.

Usage (repo root):
    python -m scripts.frontend_spec.verify_spec [paths...]    # default: docs/frontend screens, xcut
    python -m scripts.frontend_spec.verify_spec --check       # fail if findings exceed the baseline
    python -m scripts.frontend_spec.verify_spec --update      # write current counts as the baseline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIRS = ("docs/frontend/screens", "docs/frontend/xcut")
BASELINE = ROOT / "docs" / "frontend" / ".spec_verify_baseline.json"
SKIP_DIRS = {"node_modules", ".git", "dist", "__pycache__", ".venv"}

CODE_EXT = r"(?:tsx?|jsx?|py|json|ya?ml|md|sql|toml|css)"
CITE_RE = re.compile(rf"`?([A-Za-z0-9_./\\-]+\.{CODE_EXT}):(\d+)(?:-(\d+))?`?")
TICK_RE = re.compile(r"`([^`\n]+)`")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
# Identifiers that are too generic to prove anything.
GENERIC = {
    "true",
    "false",
    "null",
    "undefined",
    "string",
    "number",
    "boolean",
    "void",
    "async",
    "await",
    "const",
    "let",
    "type",
    "interface",
    "return",
    "state",
    "data",
    "error",
    "loading",
    "props",
    "children",
    "label",
    "value",
    "name",
    "title",
    "id",
    "key",
    "role",
    "alert",
    "button",
    "form",
    "input",
    "select",
    "div",
    "span",
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "None",
    "True",
    "False",
}


@dataclass
class Finding:
    file: str
    line: int
    kind: str  # missing_file | bad_line | missing_identifier | missing_section
    detail: str


@dataclass
class Report:
    path: str
    citations: int = 0
    identifiers: int = 0
    findings: list[Finding] = field(default_factory=list)


_file_cache: dict[Path, list[str] | None] = {}
_index: dict[str, list[Path]] | None = None


def _basename_index() -> dict[str, list[Path]]:
    global _index
    if _index is None:
        _index = {}
        for path in ROOT.rglob("*"):
            if path.is_file() and not (set(path.parts) & SKIP_DIRS):
                _index.setdefault(path.name, []).append(path)
    return _index


def resolve_status(cited: str) -> tuple[Path | None, str]:
    """Find the cited file: exact repo-relative path first, then a unique basename match.

    Returns (path, "ok"), (None, "missing") or (None, "ambiguous"). An ambiguous bare basename is
    the document's fault (cite a path) but not proof that the file does not exist."""
    cited = cited.replace("\\", "/").lstrip("./")
    direct = ROOT / cited
    if direct.is_file():
        return direct, "ok"
    matches = _basename_index().get(Path(cited).name, [])
    suffix = [m for m in matches if m.as_posix().endswith(cited)]
    pool = suffix or matches
    if len(pool) == 1:
        return pool[0], "ok"
    return None, ("ambiguous" if pool else "missing")


def resolve(cited: str) -> Path | None:
    return resolve_status(cited)[0]


def read_lines(path: Path) -> list[str] | None:
    if path not in _file_cache:
        try:
            _file_cache[path] = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            _file_cache[path] = None
    return _file_cache[path]


def candidate_identifiers(row: str, cited_spans: list[tuple[int, int]]) -> list[str]:
    """Backticked, code-looking tokens in the row that are not themselves citations."""
    found: list[str] = []
    for match in TICK_RE.finditer(row):
        if any(start <= match.start() < end for start, end in cited_spans):
            continue
        token = match.group(1).strip().rstrip("()")
        if token in GENERIC or len(token) < 4 or not IDENT_RE.match(token):
            continue
        found.append(token)
    return found


def check_row(doc: str, number: int, row: str, report: Report) -> None:
    cites = list(CITE_RE.finditer(row))
    if not cites:
        return
    spans = [(m.start(), m.end()) for m in cites]
    texts: list[str] = []
    for match in cites:
        report.citations += 1
        path, state = resolve_status(match.group(1))
        if path is None:
            kind = "ambiguous_file" if state == "ambiguous" else "missing_file"
            report.findings.append(Finding(doc, number, kind, match.group(1)))
            continue
        lines = read_lines(path)
        if lines is None:
            continue
        start = int(match.group(2))
        end = int(match.group(3) or start)
        if start < 1 or end < start or end > len(lines):
            report.findings.append(
                Finding(
                    doc,
                    number,
                    "bad_line",
                    f"{match.group(1)}:{start}-{end} (file has {len(lines)})",
                )
            )
        texts.append("\n".join(lines))
    if not texts:
        return
    haystack = "\n".join(texts)
    for token in candidate_identifiers(row, spans):
        report.identifiers += 1
        tail = token.split(".")[-1]
        if token not in haystack and tail not in haystack:
            report.findings.append(Finding(doc, number, "missing_identifier", token))


def check_sections(doc: str, text: str, template: str, report: Report) -> None:
    headings = re.findall(r"^## (\d+)\.", template, re.MULTILINE)
    for number in headings:
        if not re.search(rf"^#+ .*\b{number}\b", text, re.MULTILINE):
            report.findings.append(Finding(doc, 0, "missing_section", f"template section {number}"))


def verify_file(path: Path, *, require_sections: bool = False) -> Report:
    rel = path.relative_to(ROOT).as_posix()
    report = Report(rel)
    text = path.read_text(encoding="utf-8")
    in_fence = False
    for number, row in enumerate(text.splitlines(), start=1):
        if row.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and "|" in row:
            check_row(rel, number, row, report)
    if require_sections:
        template = (ROOT / "docs/frontend/SCREEN_SPEC_TEMPLATE.md").read_text(encoding="utf-8")
        check_sections(rel, text, template, report)
    return report


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in paths or list(DEFAULT_DIRS):
        target = ROOT / item
        if target.is_dir():
            files += sorted(target.rglob("*.md"))
        elif target.is_file():
            files.append(target)
    return files


def load_baseline() -> dict[str, int]:
    if not BASELINE.exists():
        return {}
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.get("findings_by_file", {}).items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    reports = [
        verify_file(p, require_sections="/screens/" in p.as_posix()) for p in collect(args.paths)
    ]
    counts = {r.path: len(r.findings) for r in reports}
    for report in reports:
        status = "OK" if not report.findings else f"{len(report.findings)} finding(s)"
        print(
            f"{report.path}: citations={report.citations} "
            f"identifiers={report.identifiers} -> {status}"
        )
        if args.verbose or (args.check and report.findings):
            for item in report.findings[:30]:
                print(f"    L{item.line} {item.kind}: {item.detail}")
    if args.update:
        BASELINE.write_text(
            json.dumps(
                {"findings_by_file": dict(sorted(counts.items()))}, indent=1, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"baseline written: {BASELINE.relative_to(ROOT).as_posix()}")
        return 0
    if args.check:
        baseline = load_baseline()
        worse = {p: n for p, n in counts.items() if n > baseline.get(p, 0)}
        if worse:
            print(f"FAIL: findings above baseline: {worse}", file=sys.stderr)
            return 1
        print("OK: no document exceeds its baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
