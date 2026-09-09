"""Code-quality-leak ratchet — ADR-2026-09-09-D Decision 2 "코드 래칫".

Tracks three counts across `src/`, `tests/`, `scripts/`:

  * ``skip_xfail``          -- ``pytest.mark.skip``/``skipif``/``xfail`` decorators and
                                ``pytest.skip()``/``pytest.xfail()`` calls
  * ``todo_fixme_xxx``      -- ``TODO``/``FIXME``/``XXX`` markers in comments
  * ``not_implemented_error`` -- ``raise NotImplementedError`` sites

Unlike `coverage_ratchet.py`/`check_type_ignore_budget.py`, the baseline in
``code-ratchets-baseline.json`` is **not** auto-updated on a green run: a
decrease is only persisted when ``--update`` is passed explicitly. A ratchet
that silently rewrites itself on every passing CI run would mask a decrease
that never got reviewed and committed by a human/worker.

A legitimate ``raise NotImplementedError`` (a fail-closed adapter stub) is
excluded from the ``not_implemented_error`` count for a whole file if that
file's first 20 lines contain a comment ``# ratchet-allow: <reason>``.

Usage: `python scripts/check_code_ratchets.py [--update]` (repo root).
Exit code: 0 = pass, 2 = a count increased beyond baseline, 1 = input error.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "code-ratchets-baseline.json"
DEFAULT_SUBDIRS = ("src", "tests", "scripts")

_EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
    }
)

_TODO_RE = re.compile(r"\b(?:TODO|FIXME|XXX)\b")
_RATCHET_ALLOW_RE = re.compile(r"#\s*ratchet-allow:\s*(\S.*)")
_HEADER_SCAN_LINES = 20

_SKIP_XFAIL_NAMES = frozenset(
    {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail", "pytest.skip", "pytest.xfail"}
)

METRICS = ("skip_xfail", "todo_fixme_xxx", "not_implemented_error")

Hit = tuple[str, int]


class CodeRatchetsError(ValueError):
    """baseline JSON 형식 오류."""


def _iter_python_files(root: Path, subdirs: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if _EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts[:-1]):
                continue
            files.append(path)
    return sorted(files)


def _ratchet_allow_reason(text: str) -> str | None:
    """파일 상단(첫 _HEADER_SCAN_LINES줄)의 'ratchet-allow: <사유>' 주석을 찾는다 --
    fail-closed 어댑터가 의도적으로 raise하는 NotImplementedError를 예외 처리하기 위함."""
    for line in text.splitlines()[:_HEADER_SCAN_LINES]:
        m = _RATCHET_ALLOW_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def _dotted_attribute_name(node: ast.Attribute) -> str | None:
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _raised_exception_name(node: ast.Raise) -> str | None:
    exc = node.exc
    if exc is None:
        return None
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def _comment_tokens(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        # 파싱 불가한 파일은 안전하게 건너뛴다(8.3 원칙).
        return []
    return out


def _scan_file(rel: str, text: str) -> dict[str, list[Hit]]:
    hits: dict[str, list[Hit]] = {m: [] for m in METRICS}

    for lineno, comment in _comment_tokens(text):
        for _ in _TODO_RE.finditer(comment):
            hits["todo_fixme_xxx"].append((rel, lineno))

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return hits

    allow_reason = _ratchet_allow_reason(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = _dotted_attribute_name(node)
            if name in _SKIP_XFAIL_NAMES:
                hits["skip_xfail"].append((rel, node.lineno))
        elif isinstance(node, ast.Raise) and allow_reason is None:
            if _raised_exception_name(node) == "NotImplementedError":
                hits["not_implemented_error"].append((rel, node.lineno))

    return hits


def scan_tree(root: Path, subdirs: tuple[str, ...] = DEFAULT_SUBDIRS) -> dict[str, list[Hit]]:
    combined: dict[str, list[Hit]] = {m: [] for m in METRICS}
    for path in _iter_python_files(root, subdirs):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        per_file = _scan_file(rel, text)
        for metric in METRICS:
            combined[metric].extend(per_file[metric])
    for metric in METRICS:
        combined[metric].sort()
    return combined


def counts_of(hits: dict[str, list[Hit]]) -> dict[str, int]:
    return {metric: len(hits[metric]) for metric in METRICS}


def read_baseline(path: Path) -> dict[str, int] | None:
    """baseline 파일이 없으면 None(최초 실행), 있으면 세 지표 값을 반환한다."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise CodeRatchetsError(f"baseline 파일이 비어 있음: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodeRatchetsError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise CodeRatchetsError("baseline JSON은 객체여야 함")
    result: dict[str, int] = {}
    for metric in METRICS:
        value = data.get(metric)
        if not isinstance(value, int) or isinstance(value, bool):
            raise CodeRatchetsError(f"baseline 값이 정수가 아님: {metric}={value!r}")
        result[metric] = value
    return result


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    payload = {metric: counts[metric] for metric in METRICS}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    parser.add_argument("--top", type=int, default=10, help="증가한 지표별로 보여줄 목록 개수")
    args = parser.parse_args(argv)

    try:
        baseline = read_baseline(args.baseline)
    except CodeRatchetsError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_tree(args.root)
    current = counts_of(hits)

    if baseline is None:
        write_baseline(args.baseline, current)
        print(f"BASELINE 초기화: {current} -> {args.baseline}")
        return 0

    increased = {m: (baseline[m], current[m]) for m in METRICS if current[m] > baseline[m]}
    if increased:
        for metric, (before, after) in increased.items():
            print(f"FAIL: {metric} {before}개 -> {after}개 (증가)")
            for rel, lineno in hits[metric][: args.top]:
                print(f"    {rel}:{lineno}")
        return 2

    decreased = {m for m in METRICS if current[m] < baseline[m]}
    if decreased and args.update:
        write_baseline(args.baseline, current)
        print(f"OK: 감소, baseline 갱신 {baseline} -> {current}")
        return 0

    if decreased:
        print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline} (현재 {current})")
        return 0

    print(f"OK: {current} (baseline {baseline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
