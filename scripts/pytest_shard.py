"""Deterministic pytest sharding — split a file list into N shards by hash.

Usage from CI::

    python scripts/pytest_shard.py --total 4 --index 0 --files-from-stdin
        < file_list.txt  # prints shard-0 file paths

The pure function ``shard(files, index, total)`` is imported by tests.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def shard(files: Sequence[str], index: int, total: int) -> list[str]:
    """Return the *index*-th shard (0-based) of *files* split among *total* shards.

    Rules
    -----
    1. **Deterministic** — same input list always yields the same shard.
    2. **Partition** — the union of all shards equals the input (no loss, no
       duplication).  Any two distinct shards share no element.
    3. **Order-preserving** — each shard keeps the original relative order of
       the files it receives.

    Algorithm
    ---------
    Sort paths lexicographically, then assign file *i* to shard ``i % total``.
    """
    if total < 1:
        raise ValueError("total must be >= 1")
    if not (0 <= index < total):
        raise ValueError(f"index {index} out of range [0, {total})")

    sorted_files = sorted(files)
    return [f for i, f in enumerate(sorted_files) if i % total == index]


# ---------------------------------------------------------------------------
# CLI entry-point (for CI or manual use)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Split files into shards.")
    parser.add_argument("--total", type=int, required=True, help="Number of shards.")
    parser.add_argument("--index", type=int, required=True, help="Shard index (0-based).")
    parser.add_argument(
        "--files-from-stdin",
        action="store_true",
        help="Read newline-separated file paths from stdin.",
    )
    parser.add_argument(
        "files", nargs="*", help="Literal file paths (fallback if not --files-from-stdin)."
    )
    args = parser.parse_args(argv)

    if args.files_from_stdin:
        files: list[str] = [line.rstrip("\n") for line in sys.stdin if line.strip()]
    else:
        files = args.files

    result = shard(files, args.index, args.total)
    for path in result:
        print(path)


if __name__ == "__main__":
    main()
