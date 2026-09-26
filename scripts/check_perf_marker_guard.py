"""Perf-marker guard (task-7434): every test in `tests/` that asserts on a
`time.perf_counter()`/`time.monotonic()` difference must carry the `perf`
marker (function, class, or module-level `pytestmark`), so it runs in the serial
perf stage instead of under xdist core contention.

Static AST scan, no DB, no imports of the scanned modules. The same scan is
asserted by `tests/unit/meta/test_perf_marker_guard.py` (which imports this
module) — this entry point exists so the local gate (pm/local_ci.py) and the
Actions verify job can run it as a plain script before pytest, the same way
`check_child_order_path.py` is wired (2026-09-26: 10 violations reached main in
one day because only the pytest form existed and the local gate did not run it).

Exit 0: no offenders. Exit 1: offenders listed as `path:function`. Exit 2: bad
`--tests-root`.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = REPO_ROOT / "tests"

_TIMER_ATTRS = frozenset({"perf_counter", "monotonic"})
_PYTESTMARK_NAME = "pytestmark"


def _mark_names(expr: ast.expr) -> set[str]:
    """Collect every `<...>.mark.<name>` attribute name reachable from `expr`.

    Covers `pytest.mark.perf`, `pytest.mark.perf(reason=...)`, and a
    `pytestmark = [pytest.mark.perf, pytest.mark.slow]` list/tuple alike,
    since `ast.walk` descends into list/tuple elements and call arguments.
    """
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


def _find_violations(tree: ast.Module) -> list[str]:
    """Return the names of unmarked test functions/methods in `tree` that
    compare a `perf_counter()`/`monotonic()` difference inside an `assert`."""
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
                    if "perf" not in own_marks and "perf" not in module_marks:
                        violations.append(node.name)

    visit(tree.body, set())
    return violations


def _test_files(tests_root: Path = TESTS_ROOT) -> list[Path]:
    return sorted(tests_root.rglob("*.py"))


def _scan_all(tests_root: Path = TESTS_ROOT) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _test_files(tests_root):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        violations = _find_violations(tree)
        if violations:
            offenders[path.relative_to(tests_root).as_posix()] = violations
    return offenders


def format_offenders(offenders: dict[str, list[str]]) -> str:
    lines = [f"{path}:{func}" for path, funcs in sorted(offenders.items()) for func in funcs]
    return (
        "wall-clock perf_counter()/monotonic() budget assertion without a `perf` marker "
        "(runs under xdist core contention -- see task-7434):\n" + "\n".join(lines)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="perf marker guard (AST scan of tests/)")
    parser.add_argument("--tests-root", type=Path, default=TESTS_ROOT)
    args = parser.parse_args(argv)
    if not args.tests_root.is_dir():
        print(f"FAIL: tests root not found: {args.tests_root}", file=sys.stderr)
        return 2
    offenders = _scan_all(args.tests_root)
    if offenders:
        print("FAIL: " + format_offenders(offenders))
        return 1
    print(
        "OK: perf marker guard — no unmarked wall-clock budget assertion under "
        f"{args.tests_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
