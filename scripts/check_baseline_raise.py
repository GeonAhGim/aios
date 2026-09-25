"""Push guard: code-language-baseline.txt must not understate the measured total.

Why (task-5303): check_code_language.py's own regression floor (added in the same leaf)
only fires when *it* produces a lowered baseline. It does nothing about a commit that
hand-edits code-language-baseline.txt directly to a smaller number without ever running
the scanner -- exactly what happened twice (4acc2620, then c2770645/task-4328): a LANG-en
leaf worker committed the file at 0. Every push after that failed against the repo's
pre-existing Hangul until a human restored the baseline by hand. This script re-measures
independently of whatever check_code_language.py did or didn't run, and rejects the push
if the committed number is less than reality -- "baseline < measured" is never a
legitimate state, only a way to make the gate permanently red for everyone downstream.

Usage: python scripts/check_baseline_raise.py [--target src] [--baseline code-language-baseline.txt]
Exit 0 = committed baseline >= measured total (push allowed).
Exit 2 = committed baseline < measured total, or unreadable (push rejected).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_code_language as ccl  # noqa: E402

DEFAULT_BASELINE = ROOT / "code-language-baseline.txt"
DEFAULT_TARGET = ROOT / "src"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    a = ap.parse_args(argv)

    total, _per_file, scanned = ccl.count_tree(a.target)
    if scanned == 0:
        print(f"FAIL: --target {a.target} contains no .py files -- refusing to trust "
              "this measurement (wrong cwd or path?).")
        return 1

    try:
        baseline = int(a.baseline.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print(f"FAIL: {a.baseline} missing or unreadable -- cannot verify it against "
              f"the measured {total}.")
        return 2

    if baseline < total:
        print(f"FAIL: {a.baseline.name}={baseline} understates the measured Hangul "
              f"comment/docstring lines {total} -- push rejected. This baseline would "
              "make the code-language gate permanently red for every push after it. "
              "Run `python scripts/check_code_language.py --update-baseline` and commit "
              "the result instead of editing the baseline file directly.")
        return 2

    print(f"OK: {a.baseline.name}={baseline} >= measured {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
