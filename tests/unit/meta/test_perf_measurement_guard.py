"""task-9121 -- root-cause gate for the `pytest_perf` 24h 4x-repeat pattern
(task-8660, task-8757, task-8934, task-9056). `check_perf_marker_guard.py`
only checks that a wall-clock perf assertion carries the `perf` marker; it
says nothing about *how* the time is measured. Two of the four repeat
incidents traced back to exactly this gap: a raw `time.perf_counter()` diff
mixes in `--cov=src` line-tracer overhead (task-7250/7253/8660) or shared-CI
scheduling noise, producing a false-positive regression that then costs a
full bisect-and-fix leaf. `tests/conftest.py`'s `perf_budget` fixture already
fixes this (process_time + tracer-pause + best-of-N); this guard flags new
`perf`-marked tests that skip it, as a warn+baseline ratchet (existing ~544
offenders grandfathered; only growth fails, see `perf-measurement-baseline.json`).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.check_perf_measurement_guard import (
    PerfMeasurementGuardError,
    _find_violations,
    _offender_count,
    _scan_all,
    main,
    read_baseline,
    write_baseline,
)


def test_current_offender_count_matches_baseline() -> None:
    """Ratchet floor: the repo-wide offender count must not exceed the
    committed baseline (`main([])` exercises the same path CI would run)."""
    assert main([]) == 0


# --- negative tests (>=3): patterns this guard must NOT flag ----------------


def test_ignores_perf_marked_test_using_perf_budget_fixture() -> None:
    source = (
        "import pytest, time\n\n"
        "@pytest.mark.perf\n"
        "def test_ok(perf_budget):\n"
        "    t = time.perf_counter()\n"
        "    do_work()\n"
        "    assert time.perf_counter() - t < 1\n"
    )
    assert _find_violations(ast.parse(source)) == []


def test_ignores_non_perf_marked_raw_timer_assertion() -> None:
    """Out of this guard's scope -- `check_perf_marker_guard.py` owns
    the missing-marker case."""
    source = (
        "import time\n\n"
        "def test_unmarked():\n"
        "    t = time.perf_counter()\n"
        "    do_work()\n"
        "    assert time.perf_counter() - t < 1\n"
    )
    assert _find_violations(ast.parse(source)) == []


def test_ignores_process_time_based_perf_marked_assertion() -> None:
    source = (
        "import pytest, time\n\n"
        "@pytest.mark.perf\n"
        "def test_ok():\n"
        "    t = time.process_time()\n"
        "    do_work()\n"
        "    assert time.process_time() - t < 1\n"
    )
    assert _find_violations(ast.parse(source)) == []


def test_flags_perf_marked_raw_timer_assertion_without_budget_fixture() -> None:
    source = (
        "import pytest, time\n\n"
        "@pytest.mark.perf\n"
        "def test_offender():\n"
        "    t = time.perf_counter()\n"
        "    do_work()\n"
        "    assert time.perf_counter() - t < 1\n"
    )
    assert _find_violations(ast.parse(source)) == ["test_offender"]


# --- gate-red reproduction: an increase over baseline must fail exit 2 -----


def test_script_main_exits_2_when_offender_count_increases(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        "import pytest, time\n\n@pytest.mark.perf\ndef test_offender():\n"
        "    t = time.perf_counter()\n    assert time.perf_counter() - t < 1\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    write_baseline(baseline, 0)
    assert main(["--tests-root", str(tmp_path), "--baseline", str(baseline)]) == 2


def test_script_main_passes_at_baseline(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        "import pytest, time\n\n@pytest.mark.perf\ndef test_offender():\n"
        "    t = time.perf_counter()\n    assert time.perf_counter() - t < 1\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    write_baseline(baseline, 1)
    assert main(["--tests-root", str(tmp_path), "--baseline", str(baseline)]) == 0


def test_script_decrease_does_not_persist_without_update(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text("def test_clean(): pass\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    write_baseline(baseline, 3)
    assert main(["--tests-root", str(tmp_path), "--baseline", str(baseline)]) == 0
    assert read_baseline(baseline) == 3


def test_script_decrease_persists_with_update(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text("def test_clean(): pass\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    write_baseline(baseline, 3)
    assert main(["--tests-root", str(tmp_path), "--baseline", str(baseline), "--update"]) == 0
    assert read_baseline(baseline) == 0


# --- failure injection: malformed baseline fails closed (exit 1, not a crash) --


def test_malformed_baseline_json_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text("def test_clean(): pass\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text("not json", encoding="utf-8")
    assert main(["--tests-root", str(tmp_path), "--baseline", str(baseline)]) == 1


def test_baseline_missing_required_key_raises(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"wrong_key": 1}), encoding="utf-8")
    with pytest.raises(PerfMeasurementGuardError):
        read_baseline(baseline)


# --- numeric performance assertion: full-tree scan stays within budget ------


@pytest.mark.perf
def test_full_tree_scan_completes_within_budget(perf_budget) -> None:
    sample = perf_budget.best_of(lambda: _offender_count(_scan_all()), n=3, warmup=1)
    print(f"[perf_measurement_guard scan] {perf_budget.describe(sample, budget_ms=45000.0)}")
    assert sample.cpu_ms < 45000.0
