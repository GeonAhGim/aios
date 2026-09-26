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

from scripts.check_perf_marker_guard import (
    TESTS_ROOT,
    _find_violations,
    _scan_all,
    format_offenders,
    main,
)

assert TESTS_ROOT.name == "tests"


def test_no_unmarked_wall_clock_perf_assertions_in_tests_tree() -> None:
    """Fails (printing file:function for every offender) if any test in
    `tests/` compares a `time.perf_counter()`/`time.monotonic()` difference
    inside an `assert` without a `perf` marker on the function, its class, or
    a module-level `pytestmark`."""
    offenders = _scan_all()
    if offenders:
        raise AssertionError(format_offenders(offenders))


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


# --- script entry point (local gate / Actions) shares the scan ---------------


def test_script_main_exits_nonzero_on_an_unmarked_offender(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        "import time\n\ndef test_slow():\n    t = time.perf_counter()\n"
        "    assert time.perf_counter() - t < 1\n",
        encoding="utf-8",
    )
    assert main(["--tests-root", str(tmp_path)]) == 1


def test_script_main_exits_zero_when_marked(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        "import pytest, time\n\n@pytest.mark.perf\ndef test_slow():\n    t = time.perf_counter()\n"
        "    assert time.perf_counter() - t < 1\n",
        encoding="utf-8",
    )
    assert main(["--tests-root", str(tmp_path)]) == 0


def test_script_main_fails_closed_on_missing_tests_root(tmp_path: Path) -> None:
    assert main(["--tests-root", str(tmp_path / "missing")]) == 2
