"""Block line-ending flips (LF -> CRLF) in changed text files.

Workers on Windows rewrite whole files through editor tools that save CRLF. A one-line change then
shows up as a whole-file diff: merge conflicts, review noise, shifted citations (2026-11-19: a
582-line diff for a 10-line change; 242 tracked files are already CRLF).

Rule: a changed text file must keep the line-ending style it has at the merge base, and a new file
must be LF. A file that was already CRLF may stay CRLF (no style change), and CRLF -> LF is allowed
(that is a normalisation).

    python scripts/check_no_eol_change.py [--base origin/main]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".sql",
    ".css",
    ".html",
)
CRLF = b"\r\n"


def _git(args: list[str], cwd: Path) -> bytes:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    return done.stdout if done.returncode == 0 else b""


def merge_base(base: str, cwd: Path) -> str:
    return _git(["merge-base", base, "HEAD"], cwd).decode("utf-8", "replace").strip()


def changed(mb: str, cwd: Path) -> list[tuple[str, str]]:
    """(status, path) of added/modified text files between the merge base and HEAD."""
    out = _git(["diff", "--name-status", "--no-renames", mb, "HEAD"], cwd)
    rows: list[tuple[str, str]] = []
    for line in out.decode("utf-8", "replace").splitlines():
        status, _, path = line.partition("\t")
        if status in ("A", "M") and path.endswith(TEXT_SUFFIXES):
            rows.append((status, path))
    return rows


def violations(mb: str, cwd: Path) -> list[str]:
    bad: list[str] = []
    for status, path in changed(mb, cwd):
        new = _git(["show", f"HEAD:{path}"], cwd)
        if CRLF not in new:
            continue
        if status == "A":
            bad.append(f"{path}: new file saved with CRLF (use LF)")
        elif CRLF not in _git(["show", f"{mb}:{path}"], cwd):
            bad.append(f"{path}: line endings flipped LF -> CRLF (keep LF)")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--repo", default=str(ROOT))
    args = parser.parse_args(argv)
    cwd = Path(args.repo)
    mb = merge_base(args.base, cwd)
    if not mb:
        print("check_no_eol_change: no merge base, skipped")
        return 0
    bad = violations(mb, cwd)
    for line in bad:
        print(f"EOL: {line}")
    if bad:
        print(
            "Fix: convert back to LF (e.g. `sed -i 's/\\r$//' <file>`) and re-save with LF. "
            "The editor tool wrote CRLF."
        )
        return 1
    print("OK: no line-ending flips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
