"""task-7434 — AST guard: every test function that asserts on a wall-clock
`time.perf_counter()`/`time.monotonic()` difference must carry the `perf`
marker (on the function itself, its enclosing class, or a module-level
`pytestmark`) so it runs in the CI verify job's serial perf stage instead of
racing other xdist workers for CPU.

Root cause (task-7434): the Actions verify job runs `-m "not perf"` under
xdist (`-n auto`) and `-m perf` serially afterwards
(`.github/workflows/quality.yml`). A wall-clock budget assertion left
unmarked runs inside the parallel stage, where core contention from sibling
workers makes the elapsed time it measures meaningless -- the test goes red
not because the code regressed but because eight workers shared the host's
cores. `time.process_time()`-based measurement (the `perf_budget` fixture in
`tests/conftest.py`) is immune to that contention and is not flagged here --
only a raw wall-clock diff is.

No allowlist -- a newly written budget-assertion test must carry the marker
from the day it is authored.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[2]
assert TESTS_ROOT.name == "tests"

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


def _test_files() -> list[Path]:
    return sorted(TESTS_ROOT.rglob("*.py"))


def _scan_all() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _test_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        violations = _find_violations(tree)
        if violations:
            offenders[path.relative_to(TESTS_ROOT).as_posix()] = violations
    return offenders


def test_no_unmarked_wall_clock_perf_assertions_in_tests_tree() -> None:
    """Fails (printing file:function for every offender) if any test in
    `tests/` compares a `time.perf_counter()`/`time.monotonic()` difference
    inside an `assert` without a `perf` marker on the function, its class, or
    a module-level `pytestmark`."""
    offenders = _scan_all()
    if offenders:
        lines = [
            f"{path}:{func}"
            for path, funcs in sorted(offenders.items())
            for func in funcs
        ]
        raise AssertionError(
            "wall-clock perf_counter()/monotonic() budget assertion without a "
            "`perf` marker (runs under xdist core contention -- see "
            "task-7434):\n" + "\n".join(lines)
        )


# --- gate-red reproduction: proves the scan above is not vacuously green ---


def test_scan_flags_a_synthetic_unmarked_perf_counter_assertion() -> None:
    source = (
        "import time\n"
        "\n"
        "def test_synthetic_offender():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    elapsed = time.perf_counter() - start\n"
        "    assert elapsed < 1.0\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == ["test_synthetic_offender"]


def test_scan_accepts_the_same_synthetic_case_once_function_marked() -> None:
    source = (
        "import time\n"
        "import pytest\n"
        "\n"
        "@pytest.mark.perf\n"
        "def test_synthetic_ok():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    elapsed = time.perf_counter() - start\n"
        "    assert elapsed < 1.0\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == []


def test_scan_accepts_module_level_pytestmark() -> None:
    source = (
        "import time\n"
        "import pytest\n"
        "\n"
        "pytestmark = pytest.mark.perf\n"
        "\n"
        "def test_synthetic_ok():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    elapsed = time.perf_counter() - start\n"
        "    assert elapsed < 1.0\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == []


def test_scan_accepts_class_level_pytestmark() -> None:
    source = (
        "import time\n"
        "\n"
        "class TestBudget:\n"
        "    pytestmark = pytest.mark.perf\n"
        "\n"
        "    def test_synthetic_ok(self):\n"
        "        start = time.perf_counter()\n"
        "        do_work()\n"
        "        elapsed = time.perf_counter() - start\n"
        "        assert elapsed < 1.0\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == []


def test_scan_ignores_perf_counter_calls_without_an_assert() -> None:
    """A test that reads the clock only for logging (no budget assertion on
    the diff) is not a contention-sensitive budget test -- not flagged."""
    source = (
        "import time\n"
        "\n"
        "def test_logs_timing_only():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    print(time.perf_counter() - start)\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == []


def test_scan_ignores_process_time_based_assertions() -> None:
    """`time.process_time()` (the `perf_budget` fixture's basis) is immune to
    xdist core contention, so it is intentionally out of this guard's scope."""
    source = (
        "import time\n"
        "\n"
        "def test_process_time_budget():\n"
        "    start = time.process_time()\n"
        "    do_work()\n"
        "    elapsed = time.process_time() - start\n"
        "    assert elapsed < 1.0\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == []


def test_scan_ignores_non_test_helper_functions() -> None:
    source = (
        "import time\n"
        "\n"
        "def _measure_once():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    elapsed = time.perf_counter() - start\n"
        "    assert elapsed < 1.0\n"
    )
    tree = ast.parse(source)
    assert _find_violations(tree) == []
