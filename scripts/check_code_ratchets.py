"""Code-quality-leak ratchet — ADR-2026-09-09-D Decision 2 "코드 래칫".

Tracks six counts across `src/`, `tests/`, `scripts/`:

  * ``skip_xfail``          -- ``pytest.mark.skip``/``skipif``/``xfail`` decorators and
                                ``pytest.skip()``/``pytest.xfail()`` calls
  * ``todo_fixme_xxx``      -- ``TODO``/``FIXME``/``XXX`` markers in comments
  * ``not_implemented_error`` -- ``raise NotImplementedError`` sites
  * ``loc_over_500``/``loc_over_800``/``loc_over_1000`` -- file-policy (ADR-2026-09-10-C
    §7) LOC observation thresholds: files whose line count exceeds 500/800/1000 lines,
    excluding files whose first 20 lines carry a ``# loc-allow: <reason>`` comment
    (generated tables, protocol mappings, deterministic rule matrices). These are
    observation aids, not a hard cap enforced by this script -- a file over 1000 lines
    without ``loc-allow`` is a reviewer/architecture-review signal (RATCHET-2, task-3256),
    counted here the same warn+baseline way as the other three metrics.

Unlike `coverage_ratchet.py`/`check_type_ignore_budget.py`, the baseline in
``code-ratchets-baseline.json`` is **not** auto-updated on a green run: a
decrease is only persisted when ``--update`` is passed explicitly. A ratchet
that silently rewrites itself on every passing CI run would mask a decrease
that never got reviewed and committed by a human/worker.

A legitimate ``raise NotImplementedError`` (a fail-closed adapter stub) is
excluded from the ``not_implemented_error`` count for a whole file if that
file's first 20 lines contain a comment ``# ratchet-allow: <reason>``.

Usage: `python scripts/check_code_ratchets.py [--update] [--near N]` (repo root).
Exit code: 0 = pass, 2 = a count increased beyond baseline, 1 = input error.

``--near N`` is an advisory-only report (does not affect exit code) listing files
that are under a loc_over_500/800/1000 threshold but within N lines of crossing it.
Run this *before* adding D2/D3 evidence (rationale prose, negative tests, ...) to a
file, since that prose is exactly what has repeatedly tipped near-threshold files
over the line after the fact (task-8905, task-9010, task-9118) -- CLAUDE.md mistake
#12 already tells a worker to "check line count first," but nothing made that check
easy to run, so it kept being skipped. This does not change loc_over_* accounting or
touch the baseline (DECISION_GUIDELINES B-2) -- it just surfaces risk before a commit
instead of after a red gate.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
from concurrent.futures import ThreadPoolExecutor
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

# esc-ci-code_ratchets ([code_ratchets] timeout 180s, bisect culprit 2dd74ce9 -- a DEEPEN
# commit that grew the tracked-file count under tests/): a serial path.read_text() over
# ~3,100 src/tests/scripts .py files took ~117s wall-clock on a cold-cache checkout even
# though ast.parse itself is near-instant (user time ~0.06s of that) -- almost all of it was
# blocking disk I/O per open(). Same root cause and fix as check_no_bom.py's SCAN_WORKERS
# saga: I/O-bound reads release the GIL, so a shared thread pool overlaps that per-file
# latency instead of paying it serially. 16 matches check_no_bom.py's fleet-tuned value
# (half the ThreadPoolExecutor library default of min(32, cpu_count+4)) -- that value was
# chosen there to give cold-checkout headroom without the burst of concurrent OS threads
# that starved sibling process-creation under this fleet's antivirus scanning when every
# worker lane's step used the library default at once. No baseline/threshold change
# (DECISION_GUIDELINES B-2) -- this only retunes this step's own I/O concurrency.
SCAN_WORKERS = 16

_TODO_RE = re.compile(r"\b(?:TODO|FIXME|XXX)\b")
_RATCHET_ALLOW_RE = re.compile(r"#\s*ratchet-allow:\s*(\S.*)")
_LOC_ALLOW_RE = re.compile(r"#\s*loc-allow:\s*(\S.*)")
_HEADER_SCAN_LINES = 20
_LOC_THRESHOLDS = (500, 800, 1000)

_SKIP_XFAIL_NAMES = frozenset(
    {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail", "pytest.skip", "pytest.xfail"}
)

METRICS = (
    "skip_xfail",
    "todo_fixme_xxx",
    "not_implemented_error",
    "loc_over_500",
    "loc_over_800",
    "loc_over_1000",
)

Hit = tuple[str, int]


class CodeRatchetsError(ValueError):
    """baseline JSON 형식 오류."""


def _iter_python_files(root: Path, subdirs: tuple[str, ...]) -> list[Path]:
    """os.walk with in-place pruning so excluded directories (__pycache__, .venv, ...) are
    never descended into -- rglob("*.py") lists everything first and filters afterward,
    which still pays the traversal cost of every pruned subtree."""
    files: list[Path] = []
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIR_NAMES]
            for name in filenames:
                if name.endswith(".py"):
                    files.append(Path(dirpath) / name)
    return sorted(files)


def _ratchet_allow_reason(text: str) -> str | None:
    """파일 상단(첫 _HEADER_SCAN_LINES줄)의 'ratchet-allow: <사유>' 주석을 찾는다 --
    fail-closed 어댑터가 의도적으로 raise하는 NotImplementedError를 예외 처리하기 위함."""
    for line in text.splitlines()[:_HEADER_SCAN_LINES]:
        m = _RATCHET_ALLOW_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def _loc_allow_reason(text: str) -> str | None:
    """파일 상단(첫 _HEADER_SCAN_LINES줄)의 'loc-allow: <사유>' 주석을 찾는다 --
    ADR-2026-09-10-C §7(생성 테이블/프로토콜 매핑/결정론적 규칙 행렬)의 LOC 하드캡
    예외를 위함."""
    for line in text.splitlines()[:_HEADER_SCAN_LINES]:
        m = _LOC_ALLOW_RE.search(line)
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

    if _loc_allow_reason(text) is None:
        loc = len(text.splitlines())
        for threshold in _LOC_THRESHOLDS:
            if loc > threshold:
                hits[f"loc_over_{threshold}"].append((rel, loc))

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


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def scan_tree(root: Path, subdirs: tuple[str, ...] = DEFAULT_SUBDIRS) -> dict[str, list[Hit]]:
    combined: dict[str, list[Hit]] = {m: [] for m in METRICS}
    files = _iter_python_files(root, subdirs)
    # I/O-bound: a serial path.read_text() per file over ~3,100 files paid full disk latency
    # on every open() on a cold checkout (esc-ci-code_ratchets timeout 180s). A shared thread
    # pool overlaps that wait the same way check_no_bom.py's SCAN_WORKERS pool does.
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        texts = pool.map(_read_file, files)
    for path, text in zip(files, texts, strict=True):
        rel = path.relative_to(root).as_posix()
        per_file = _scan_file(rel, text)
        for metric in METRICS:
            combined[metric].extend(per_file[metric])
    for metric in METRICS:
        combined[metric].sort()
    return combined


def counts_of(hits: dict[str, list[Hit]]) -> dict[str, int]:
    return {metric: len(hits[metric]) for metric in METRICS}


def scan_locs(root: Path, subdirs: tuple[str, ...] = DEFAULT_SUBDIRS) -> list[Hit]:
    """Per-file line counts for every tracked file, excluding ``loc-allow`` files --
    used by ``--near`` to warn about files approaching a threshold *before* a commit
    pushes them over it, instead of discovering the loc_over_500 regression only after
    it already failed the gate (task-8905/task-9010/task-9118: mandatory D2/D3 rationale
    prose repeatedly tipped files that were already close to 500 lines)."""
    files = _iter_python_files(root, subdirs)
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        texts = pool.map(_read_file, files)
    out: list[Hit] = []
    for path, text in zip(files, texts, strict=True):
        if _loc_allow_reason(text) is not None:
            continue
        rel = path.relative_to(root).as_posix()
        out.append((rel, len(text.splitlines())))
    return out


def near_threshold_files(locs: list[Hit], within: int) -> dict[int, list[Hit]]:
    """Files under a threshold but within ``within`` lines of crossing it, per
    threshold, sorted closest-to-crossing first."""
    result: dict[int, list[Hit]] = {t: [] for t in _LOC_THRESHOLDS}
    for rel, loc in locs:
        for threshold in _LOC_THRESHOLDS:
            if threshold - within <= loc <= threshold:
                result[threshold].append((rel, loc))
    for threshold in _LOC_THRESHOLDS:
        result[threshold].sort(key=lambda item: -item[1])
    return result


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
    parser.add_argument(
        "--near",
        type=int,
        default=0,
        help=(
            "0보다 크면, 아직 위반이 아니지만 그 줄 수 안으로 임계값(500/800/1000)에 "
            "근접한 파일을 함께 보고한다(D3 증빙 추가 전에 먼저 실행할 것). "
            "종료 코드에는 영향 없음."
        ),
    )
    args = parser.parse_args(argv)

    try:
        baseline = read_baseline(args.baseline)
    except CodeRatchetsError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_tree(args.root)
    current = counts_of(hits)

    if args.near > 0:
        near = near_threshold_files(scan_locs(args.root), args.near)
        for threshold in _LOC_THRESHOLDS:
            files = near[threshold]
            if not files:
                continue
            print(f"NEAR loc_over_{threshold} (임계값 {args.near}줄 이내, 아직 위반 아님):")
            for rel, loc in files[: args.top]:
                print(f"    {rel}:{loc} ({threshold - loc}줄 남음)")

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
