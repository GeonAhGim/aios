"""task-6566(esc-ci-pytest.json) — root-cause fix for `tests/conftest.py`'s
`pytest_sessionfinish` hook (D2 red-gate reproduction).

`C:\\aios\\pm\\local_ci.py`'s commit mode passes changed test files straight to
pytest (`ci_impact.select_impacted_tests`: a changed path under `tests/` is its
own impact). If every test in that file carries one of pyproject.toml's
default-excluded markers (`nightly`/`live_demo`/`redis`, see `addopts`), pytest
collects them, deselects all of them, and exits `ExitCode.NO_TESTS_COLLECTED`
(5) even though nothing is actually broken — that misclassification is the
literal esc-ci-pytest.json repro (`tests/e2e/bitget_demo/
test_bitget_demo_pipeline.py`, task-6176..task-6566, a dozen+ recurrences).

These tests run real subprocesses against this repo (not `tmp_path`, which
would sit outside the tree and never pick up `tests/conftest.py`) to prove the
hook fires exactly where it should and nowhere else.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BITGET_DEMO_FILE = "tests/e2e/bitget_demo/test_bitget_demo_pipeline.py"
_TIMEOUT_FILE = "tests/unit/test_per_test_timeout_kill_scope.py"


def _run_pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=_REPO_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_fully_live_demo_deselected_file_exits_ok_not_no_tests_collected() -> None:
    """Red-gate reproduction — esc-ci-pytest.json's exact `[pytest] \\n4
    deselected in 0.50s` output. Every test in this file is `live_demo`
    (excluded by default addopts); before the fix this was rc=5."""
    result = _run_pytest(_BITGET_DEMO_FILE)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "deselected" in (result.stdout + result.stderr)


def test_genuinely_empty_k_filter_still_reports_no_tests_collected() -> None:
    """Negative #1 — a `-k` typo that matches nothing on an unmarked file must
    still fail loudly. The hook must not blanket-suppress every
    `NO_TESTS_COLLECTED`, only the addopts-marker-driven case."""
    result = _run_pytest("-k", "definitely_not_a_real_test_xyz_zzz", _TIMEOUT_FILE)

    assert result.returncode == 5, result.stdout + result.stderr


def test_mixed_deselection_with_unmarked_items_still_fails() -> None:
    """Negative #2 — if the deselected set contains items that do NOT carry a
    default-excluded marker (mixed with a `-k` miss on an unrelated,
    unmarked file), the hook must not paper over that: something in the
    selection was excluded for a reason other than the known markers, so it
    stays red."""
    result = _run_pytest(
        "-k",
        "definitely_not_a_real_test_xyz_zzz",
        _BITGET_DEMO_FILE,
        _TIMEOUT_FILE,
    )

    assert result.returncode == 5, result.stdout + result.stderr


def test_nonexistent_nodeid_still_reports_usage_error() -> None:
    """Negative #3 — a node id that does not exist collects zero items
    outright (nothing to deselect, `reporter.stats["deselected"]` stays
    empty). The hook's early-return on an empty `deselected` list must leave
    this untouched."""
    result = _run_pytest(f"{_TIMEOUT_FILE}::test_does_not_exist_at_all")

    assert result.returncode == 4, result.stdout + result.stderr
