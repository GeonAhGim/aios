"""Perf-measurement guard (task-9121, pytest_perf 24h 4x repeat root cause).

`check_perf_marker_guard.py` only checks that a wall-clock-based perf assertion
carries `@pytest.mark.perf` (so it runs in the serial stage instead of under
xdist core contention) -- it says nothing about *how* the time is measured.
2 of the last 4 `pytest_perf` 24h-repeat incidents (task-8660, and the
coverage-tracer pattern already fixed once in task-7250/7253) were exactly
this: a test asserts on a raw `time.perf_counter()`/`time.monotonic()` diff,
which mixes in `--cov=src` line-tracer overhead and OS scheduler noise from
other processes on the shared CI machine. `tests/conftest.py`'s `perf_budget`
fixture (task-6774) already fixes this -- `time.process_time()` + tracer-pause
+ best-of-N -- but adoption across the ~630 `perf`-marked tests is partial, so
new tests keep entering with the same raw-timer pattern that has already
caused repeat incidents, and will keep doing so as the marked-test count grows
(every DEEPEN leaf's D2 "numeric performance assertion" requirement adds one).

This is a warn+baseline ratchet like `check_code_ratchets.py`: existing
offenders are grandfathered (`perf-measurement-baseline.json`), only an
*increase* fails. Fixing the ~400 existing offenders is out of scope for one
leaf (task-9121) -- register this gate with ops as `STEP_MODE=warn` (see
CLAUDE.md OPS-42) before wiring it into CI so main does not lock red on
introduction (Frequent mistake #6).

Detection: AST scan (no imports, no DB) of `tests/**/*.py` for a `test*`
function that (a) carries the `perf` marker (function/class/module-level,
same logic as `check_perf_marker_guard.py`), (b) contains a raw
`perf_counter()`/`monotonic()` call feeding an `assert`, and (c) does **not**
take `perf_budget` as a parameter (the one sanctioned way to route the
comparison through `PerfBudget`, which pauses the coverage tracer and uses
`process_time()`). A test can still call `perf_counter()` for a `print()`
diagnostic without tripping this -- only a raw-timer diff reaching an `assert`
counts, matching `check_perf_marker_guard.py`'s own `_contains_assert` scope.

Usage: `python scripts/check_perf_measurement_guard.py [--update]` (repo root).
Exit code: 0 = pass (no increase), 2 = offender count increased, 1 = input error.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = ROOT / "tests"
DEFAULT_BASELINE = ROOT / "perf-measurement-baseline.json"

_TIMER_ATTRS = frozenset({"perf_counter", "monotonic"})
_PYTESTMARK_NAME = "pytestmark"
_BUDGET_PARAM = "perf_budget"


class PerfMeasurementGuardError(Exception):
    """baseline 파일 형식 오류."""


def _mark_names(expr: ast.expr) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(expr):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
        ):
            names.add(node.attr)
    return names


def _decorator_mark_names(decorators: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for dec in decorators:
        names |= _mark_names(dec)
    return names


def _pytestmark_assign_mark_names(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        if any(isinstance(t, ast.Name) and t.id == _PYTESTMARK_NAME for t in targets):
            names |= _mark_names(value)
    return names


def _is_timer_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in _TIMER_ATTRS
    if isinstance(func, ast.Name):
        return func.id in _TIMER_ATTRS
    return False


def _contains_timer_call(node: ast.AST) -> bool:
    return any(_is_timer_call(n) for n in ast.walk(node))


def _contains_assert(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Assert) for n in ast.walk(node))


def _has_budget_param(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = fn.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    return _BUDGET_PARAM in names


def _find_violations(tree: ast.Module) -> list[str]:
    """Return `function` names of perf-marked tests that assert on a raw
    timer diff without routing it through the `perf_budget` fixture."""
    module_marks = _pytestmark_assign_mark_names(tree.body)
    violations: list[str] = []

    def visit(body: list[ast.stmt], inherited_marks: set[str]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                class_marks = (
                    inherited_marks
                    | _decorator_mark_names(node.decorator_list)
                    | _pytestmark_assign_mark_names(node.body)
                )
                visit(node.body, class_marks)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.name.startswith("test")
                    and _contains_timer_call(node)
                    and _contains_assert(node)
                ):
                    own_marks = inherited_marks | _decorator_mark_names(node.decorator_list)
                    is_perf = "perf" in own_marks or "perf" in module_marks
                    if is_perf and not _has_budget_param(node):
                        violations.append(node.name)

    visit(tree.body, set())
    return violations


def _test_files(tests_root: Path = TESTS_ROOT) -> list[Path]:
    paths = [
        Path(dirpath) / name
        for dirpath, _dirnames, filenames in os.walk(tests_root)
        for name in filenames
        if name.endswith(".py")
    ]
    return sorted(paths)


def _scan_all(tests_root: Path = TESTS_ROOT) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _test_files(tests_root):
        source = path.read_text(encoding="utf-8")
        if not any(attr in source for attr in _TIMER_ATTRS):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        violations = _find_violations(tree)
        if violations:
            offenders[path.relative_to(tests_root).as_posix()] = violations
    return offenders


def _offender_count(offenders: dict[str, list[str]]) -> int:
    return sum(len(v) for v in offenders.values())


def format_offenders(offenders: dict[str, list[str]]) -> str:
    lines = [f"{path}:{func}" for path, funcs in sorted(offenders.items()) for func in funcs]
    return (
        "perf-marked test asserts on a raw perf_counter()/monotonic() diff instead of the "
        "noise-resistant `perf_budget` fixture (see tests/conftest.py PerfBudget, task-6774; "
        "task-9121):\n" + "\n".join(lines)
    )


def read_baseline(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PerfMeasurementGuardError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict) or "raw_timer_perf_asserts" not in data:
        raise PerfMeasurementGuardError("baseline JSON은 raw_timer_perf_asserts 키를 가져야 함")
    value = data["raw_timer_perf_asserts"]
    if not isinstance(value, int):
        raise PerfMeasurementGuardError(f"baseline 값이 정수가 아님: {value!r}")
    return value


def write_baseline(path: Path, count: int) -> None:
    path.write_text(
        json.dumps({"raw_timer_perf_asserts": count}, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-root", type=Path, default=TESTS_ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    args = parser.parse_args(argv)

    if not args.tests_root.is_dir():
        print(f"FAIL: tests root not found: {args.tests_root}", file=sys.stderr)
        return 1

    offenders = _scan_all(args.tests_root)
    current = _offender_count(offenders)

    try:
        baseline = read_baseline(args.baseline)
    except PerfMeasurementGuardError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if baseline is None:
        write_baseline(args.baseline, current)
        print(f"BASELINE 초기화: {current} -> {args.baseline}")
        return 0

    if current > baseline:
        print(f"FAIL: raw_timer_perf_asserts {baseline} -> {current} (증가)")
        print(format_offenders(offenders))
        return 2

    if current < baseline:
        if args.update:
            write_baseline(args.baseline, current)
            print(f"OK: 감소, baseline 갱신 {baseline} -> {current}")
        else:
            print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline} (현재 {current})")
        return 0

    print(f"OK: {current} (baseline {baseline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
