"""Code-comment language ratchet: comments and docstrings in `src/` must be English.

Why (measured on 2026-09-07, not a style opinion):

1. `ruff` counts Hangul as display width 2, so a line that reads as 80 characters
   fails E501 at 100. Six such failures turned CI red in a single file.
2. A `print()` containing Hangul raises `UnicodeEncodeError` on a cp949 console.
   This crashed the self-restart path and the audit checker on the same day.
3. Mixed-script source makes grep/regex over identifiers and prose unreliable.

Scope decision (ADR-2026-09-07-A):
  * English  : comments and docstrings under `src/`
  * Korean   : docs/, ADRs, specs, commit messages, task notes, user-facing product strings
  * Untouched: the 14,760 existing Hangul lines are NOT retro-converted. Rewriting 766 files
    while eight workers commit concurrently costs more than it returns and destroys blame.

Mechanism is the same ratchet as `coverage_ratchet.py`: the count may fall, never rise.
Only comments and docstrings are counted. Ordinary string literals are product text and are
excluded, because those go through i18n instead.

Usage: `python scripts/check_code_language.py`. Exit code 0 = pass.
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "code-language-baseline.txt"
DEFAULT_TARGET = ROOT / "src"
HANGUL = re.compile(r"[가-힣]")


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Line numbers occupied by docstrings (module, class, function)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            continue
        start = first.lineno
        end = getattr(first, "end_lineno", start) or start
        out.update(range(start, end + 1))
    return out


def count_file(path: Path) -> int:
    """Hangul-bearing comment or docstring lines in one file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    lines = text.splitlines()
    flagged: set[int] = set()

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    doc_lines = _docstring_lines(tree)
    for n in doc_lines:
        if 1 <= n <= len(lines) and HANGUL.search(lines[n - 1]):
            flagged.add(n)

    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and HANGUL.search(tok.string):
                flagged.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return len(flagged)


def count_tree(target: Path) -> tuple[int, list[tuple[str, int]], int]:
    total = 0
    scanned = 0
    per_file: list[tuple[str, int]] = []
    for p in sorted(target.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        scanned += 1
        n = count_file(p)
        if n:
            per_file.append((p.relative_to(ROOT).as_posix(), n))
            total += n
    per_file.sort(key=lambda x: -x[1])
    return total, per_file, scanned


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--top", type=int, default=10, help="show the N worst files")
    ap.add_argument("--update-baseline", action="store_true",
                     help="write the measured total to --baseline when it legitimately "
                          "improves (or bootstrap a missing baseline). Without this flag "
                          "the script only ever reports, it never writes.")
    a = ap.parse_args(argv)

    total, per_file, scanned = count_tree(a.target)

    # task-4966: a run against a wrong/empty --target (e.g. invoked from the wrong cwd
    # with a relative path) silently scanned 0 files, measured total=0, and this treated
    # it as a legitimate improvement and lowered the baseline to 0 (4acc2620) -- every
    # subsequent push then failed against the unrelated pre-existing Hangul in the repo.
    # A target that yields no .py files at all is never a real measurement.
    if scanned == 0:
        print(f"FAIL: --target {a.target} contains no .py files -- refusing to trust "
              "this measurement (wrong cwd or path?). Baseline left untouched.")
        return 1

    try:
        baseline: int | None = int(a.baseline.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        baseline = None

    # task-5303: 4acc2620 recurred as c2770645(task-4328) -- a LANG-en leaf worker
    # committed code-language-baseline.txt=0 directly, not through this script, and the
    # gate stayed permanently red until a human restored it by hand. scanned>0 alone
    # does not prove the measurement is trustworthy: a total of 0, or a total far below
    # the last known-good baseline, is far more likely to be a broken run (wrong cwd,
    # partial checkout, ast/tokenize regression) than 11k+ lines of Hangul vanishing in
    # one leaf. Refuse to trust either, and never write the baseline for them, with or
    # without --update-baseline.
    if total == 0 or (baseline is not None and baseline > 0 and total < baseline * 0.5):
        print(f"FAIL: measured {total} Hangul comment/docstring lines is implausible "
              f"(baseline {baseline}) -- refusing to write the baseline. Confirm this by "
              "hand before touching code-language-baseline.txt; a run this low is almost "
              "always a broken measurement, not a real fix.")
        return 2

    if baseline is None:
        if not a.update_baseline:
            print(f"FAIL: no baseline at {a.baseline} -- re-run with --update-baseline to "
                  f"bootstrap it at the measured {total}.")
            return 2
        a.baseline.write_text(f"{total}\n", encoding="utf-8")
        print(f"OK: baseline created at {total}")
        return 0

    if total > baseline:
        print(f"FAIL: Hangul comment/docstring lines {baseline} -> {total} (limit exceeded)")
        print("Write new comments and docstrings in English (ADR-2026-09-07-A).")
        print("Existing Korean is not retro-converted; only growth is blocked.")
        for name, n in per_file[:a.top]:
            print(f"    {n:>5}  {name}")
        return 1

    if total < baseline:
        if not a.update_baseline:
            print(f"OK: Hangul comment/docstring lines reduced {baseline} -> {total} "
                  "(re-run with --update-baseline to record it)")
            return 0
        a.baseline.write_text(f"{total}\n", encoding="utf-8")
        print(f"OK: reduced, baseline updated {baseline} -> {total}")
        return 0

    print(f"OK: Hangul comment/docstring lines {total} (baseline {baseline})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
