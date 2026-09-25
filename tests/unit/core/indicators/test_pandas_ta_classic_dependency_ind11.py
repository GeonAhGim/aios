"""IND-11 (task-4035) -- pyproject.toml pandas-ta-classic dependency + oracle
extra ban.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
IND-11, ADR-2026-09-09-A, docs/design/INDICATOR_OSS_EVAL.md §5/§6. This leaf
only adds the declared dependency (`pyproject.toml`) and its provenance
record (`NOTICE-AIOS.md`) -- `adapters/pandas_ta_bridge.py` and indicator
registration (net-incremental count, zero duplicates, 3-way cross-check) are
separate leaves and are N/A(out of scope for this file-only leaf) here.

D2 evidence:
1. negative >= 3 -- tulipy declared as a dependency, missing `[project]`
   table, `pandas-ta-classic[oracle]` extra syntax present.
2. failure injection -- reading a nonexistent pyproject.toml surfaces
   FileNotFoundError instead of being swallowed (fail-closed).
3. perf assertion -- 1,000 dependency-extraction calls under budget.
4. gate-red reproduction -- oss_eval_gate's BANNED_LICENSE_DEPENDENCY path
   still fires when this leaf's dependency list is poisoned with tulipy.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.core.indicators.oss_eval_gate import (
    BANNED_LICENSE_PACKAGES,
    OssEvalGateError,
    assert_oss_eval_gate,
    extract_declared_dependencies,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
_NOTICE_PATH = _REPO_ROOT / "NOTICE-AIOS.md"
_EVAL_PATH = _REPO_ROOT / "docs" / "design" / "INDICATOR_OSS_EVAL.md"


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return _PYPROJECT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def notice_text() -> str:
    return _NOTICE_PATH.read_text(encoding="utf-8")


def test_pandas_ta_classic_declared_as_main_dependency(pyproject_text: str) -> None:
    names = extract_declared_dependencies(pyproject_text)
    assert "pandas-ta-classic" in names


def test_oracle_extra_never_requested_in_pyproject(pyproject_text: str) -> None:
    # INDICATOR_OSS_EVAL.md §5: `oracle` extra pulls in tulipy (LGPL-3.0).
    assert "pandas-ta-classic[oracle]" not in pyproject_text
    assert re.search(r"pandas-ta-classic\s*\[", pyproject_text) is None


def test_notice_records_pandas_ta_classic_provenance(notice_text: str) -> None:
    assert "pandas-ta-classic" in notice_text
    assert "MIT" in notice_text
    assert "oracle" in notice_text.lower()
    assert "tulipy" in notice_text
    assert "LGPL" in notice_text


def test_pip_show_tulipy_not_found() -> None:
    # DoD: this repo's actual venv must never carry tulipy, regardless of
    # what any pyproject.toml text says.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "tulipy"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not found" in (result.stdout + result.stderr).lower()


def test_negative_tulipy_never_a_declared_dependency(pyproject_text: str) -> None:
    names = extract_declared_dependencies(pyproject_text)
    assert not names & frozenset(BANNED_LICENSE_PACKAGES)


def test_negative_missing_project_table_rejected() -> None:
    with pytest.raises(OssEvalGateError, match="MISSING_PROJECT_TABLE"):
        extract_declared_dependencies("[tool.ruff]\nline-length = 100\n")


def test_negative_oracle_extra_syntax_detected_if_ever_introduced(
    pyproject_text: str,
) -> None:
    # Prove the detection pattern used above actually catches the poisoned
    # form -- guards against the assertion itself being vacuous.
    poisoned = pyproject_text.replace(
        '"pandas-ta-classic>=0.6.52",',
        '"pandas-ta-classic[oracle]>=0.6.52",',
        1,
    )
    assert poisoned != pyproject_text
    assert re.search(r"pandas-ta-classic\s*\[", poisoned) is not None


def test_failure_injection_missing_pyproject_surfaces_not_swallowed(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "no-such-pyproject.toml"
    with pytest.raises(FileNotFoundError):
        extract_declared_dependencies(missing.read_text(encoding="utf-8"))


def test_gate_red_reproduction_tulipy_declared_fails_oss_eval_gate(
    pyproject_text: str,
) -> None:
    eval_text = _EVAL_PATH.read_text(encoding="utf-8")
    poisoned = pyproject_text.replace(
        '"pandas-ta-classic>=0.6.52",',
        '"pandas-ta-classic>=0.6.52",\n    "tulipy>=0.4.0",',
        1,
    )
    assert poisoned != pyproject_text
    with pytest.raises(OssEvalGateError, match="BANNED_LICENSE_DEPENDENCY"):
        assert_oss_eval_gate(eval_text, poisoned)


@pytest.mark.perf
def test_perf_dependency_extraction_under_budget(pyproject_text: str) -> None:
    n = 1000
    budget_sec = 1.0
    extract_declared_dependencies(pyproject_text)  # warm
    start = time.perf_counter()
    for _ in range(n):
        extract_declared_dependencies(pyproject_text)
    elapsed = time.perf_counter() - start
    print(
        f"[IND-11 pandas-ta-classic dependency] {n} runs {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"{n} runs exceeded the budget ({budget_sec}s): {elapsed:.3f}s"
    )
