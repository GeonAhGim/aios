"""FA-19 -- static audit gate: raw `INSERT INTO` SQL that ships without an
`ON CONFLICT` clause (or an explicit `# idempotent-allow:` justification) is a
non-idempotent write -- a retried request/consumer duplicates the row instead
of being a no-op.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-19
(table row 133, "idempotent-retry full audit + static check", DoD "0
non-idempotent writes").

Scope and method mirror `check_position_key_central.py`: an AST scanner, not
a semantic prover. It finds calls shaped like
``conn.execute("INSERT INTO ...")``/``fetchval``/``fetchrow``/``fetch``/
``executemany`` on a string literal (adjacent string literals are folded
into one `ast.Constant` by the parser, so multi-line SQL is covered; an
f-string/`JoinedStr` first argument is skipped -- its table/columns can't be
read statically, so this scanner cannot make a claim about it either way).

A hit is a *candidate* non-idempotent write unless one of:

1. The SQL text itself contains ``ON CONFLICT`` (case-insensitive) -- the
   INSERT is already an upsert, safe to retry by construction.
2. The call's line, or the line immediately before it, carries a
   ``# idempotent-allow: <reason>`` comment -- the author has reviewed the
   call site and recorded *why* re-running it is safe (e.g. it runs inside a
   claim-first transaction, or targets an append-only table whose
   uniqueness is enforced by a separate `idempotency_key`/digest column
   checked before this INSERT is reached -- see `post_entry.py`'s
   `find_by_idempotency_key` guard for the canonical example this scanner
   cannot see statically).

`src/db/migrations/` is excluded (same rationale as
`check_position_key_central.py`: one-off historical DDL/DML, not an
application write path). `tests/` is excluded (fixtures, not production write
paths).

Baseline (OPS-42 warn-then-gate rollout): a brand-new gate over a large
existing codebase must not hard-fail `main` on introduction. This script
follows the same ratchet contract as `check_code_ratchets.py` --
`idempotent-writes-baseline.json` freezes the current count; a run that
finds *more* unexempted hits than the baseline fails (exit 2), a run that
finds fewer can be persisted with ``--update``. Baseline entries are not a
free pass forever: each is expected to earn a real `# idempotent-allow:`
review (or a fix) as adjacent leaves touch that file, converging the count to
zero over time rather than in one leaf (task-2676 decision).

Usage: `python scripts/check_idempotent_writes.py [--update]` (repo root).
Exit code: 0 = pass, 2 = unexempted hits exceed baseline, 1 = input error.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "idempotent-writes-baseline.json"
DEFAULT_SCAN_ROOTS = ("src", "scripts")

_EXCLUDE_DIR_PARTS = (
    ("src", "db", "migrations"),
)
_EXCLUDE_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
_SELF_PATH = ("scripts", "check_idempotent_writes.py")

_WRITE_METHODS = frozenset({"execute", "executemany", "fetch", "fetchval", "fetchrow", "fetchmany"})
_ALLOW_MARKER = "# idempotent-allow:"


@dataclass(frozen=True)
class Hit:
    location: str
    line: int
    reason: str

    def key(self) -> tuple[str, int]:
        return (self.location, self.line)

    def __str__(self) -> str:
        return f"{self.location}:{self.line} — {self.reason}"


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    if rel_parts == _SELF_PATH:
        return True
    if any(rel_parts[: len(prefix)] == prefix for prefix in _EXCLUDE_DIR_PARTS):
        return True
    return bool(_EXCLUDE_DIR_NAMES & set(rel_parts[:-1]))


def _sql_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_uncontested_insert(sql: str) -> bool:
    normalized = " ".join(sql.split()).lower()
    return normalized.startswith("insert into") and "on conflict" not in normalized


def _comment_lines(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return {}
    return out


def _has_allow_marker(comments: dict[int, str], lineno: int) -> bool:
    for candidate in (lineno, lineno - 1):
        comment = comments.get(candidate)
        if comment is not None and _ALLOW_MARKER in comment:
            return True
    return False


def _scan_source(source: str, location: str) -> list[Hit]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    comments = _comment_lines(source)
    hits: list[Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _WRITE_METHODS):
            continue
        if not node.args:
            continue
        sql = _sql_literal(node.args[0])
        if sql is None or not _is_uncontested_insert(sql):
            continue
        if _has_allow_marker(comments, node.lineno):
            continue
        hits.append(
            Hit(
                location=location,
                line=node.lineno,
                reason=f"INSERT INTO without ON CONFLICT via .{func.attr}(...)",
            )
        )
    return hits


def scan_tree(root: Path, scan_roots: tuple[str, ...] = DEFAULT_SCAN_ROOTS) -> list[Hit]:
    hits: list[Hit] = []
    for sub in scan_roots:
        base = root / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root)
            if _is_excluded(rel.parts):
                continue
            hits.extend(_scan_source(path.read_text(encoding="utf-8"), rel.as_posix()))
    hits.sort(key=Hit.key)
    return hits


class BaselineError(ValueError):
    """baseline JSON 형식 오류."""


def read_baseline(path: Path) -> int | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise BaselineError(f"baseline 파일이 비어 있음: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict) or "count" not in data:
        raise BaselineError("baseline JSON은 {'count': int, ...} 형태여야 함")
    count = data["count"]
    if not isinstance(count, int) or isinstance(count, bool):
        raise BaselineError(f"baseline count가 정수가 아님: {count!r}")
    return count


def write_baseline(path: Path, hits: list[Hit]) -> None:
    payload = {
        "count": len(hits),
        "hits": [f"{h.location}:{h.line}" for h in hits],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    parser.add_argument("--top", type=int, default=20, help="보여줄 위반 목록 개수")
    args = parser.parse_args(argv)

    try:
        baseline = read_baseline(args.baseline)
    except BaselineError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_tree(args.root)
    current = len(hits)

    if baseline is None:
        write_baseline(args.baseline, hits)
        print(f"BASELINE 초기화: {current}건 -> {args.baseline}")
        return 0

    if current > baseline:
        print(f"FAIL: idempotent_writes {baseline}건 -> {current}건 (증가)")
        for hit in hits[: args.top]:
            print(f"    {hit}")
        return 2

    if current < baseline and args.update:
        write_baseline(args.baseline, hits)
        print(f"OK: 감소, baseline 갱신 {baseline}건 -> {current}건")
        return 0

    if current < baseline:
        print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline}건 (현재 {current}건)")
        return 0

    print(f"OK: {current}건 (baseline {baseline}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
