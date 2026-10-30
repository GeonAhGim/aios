"""coverage_ratchet 성능 예산 검출력 — 예산 10배 지연 주입 시 적색(gate-red 재현)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "coverage_ratchet.py"
_spec = importlib.util.spec_from_file_location("coverage_ratchet_perf_red", _SCRIPT)
assert _spec is not None and _spec.loader is not None
coverage_ratchet = importlib.util.module_from_spec(_spec)
sys.modules["coverage_ratchet_perf_red"] = coverage_ratchet
_spec.loader.exec_module(coverage_ratchet)


def _xml(tmp_path: Path, line_rate: float) -> Path:
    path = tmp_path / "coverage.xml"
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{line_rate}" branch-rate="0"></coverage>',
        encoding="utf-8",
    )
    return path


def test_read_baseline_percent_budget_injection_red(
    tmp_path: Path, perf_budget: PerfBudget
) -> None:
    """예산 1ms의 10배(10ms) 지연 주입 → AssertionError."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("75.00\n", encoding="utf-8")
    delayed_fn = virtual_delayed_sync(coverage_ratchet.read_baseline_percent, 0.01)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(lambda: delayed_fn(path), budget_ms=1, batch=100)


@pytest.mark.parametrize("extra", [["--allow-baseline-write"], []])
def test_main_budget_injection_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget, extra: list[str]
) -> None:
    """예산 50ms의 10배(500ms) 지연 주입 → AssertionError (첫 실행/실패 경로 모두)."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    baseline = tmp_path / "coverage-baseline.txt"
    if not extra:
        baseline.write_text("80.00\n", encoding="utf-8")
    args = ["--coverage-xml", str(_xml(tmp_path, 0.70)), "--baseline", str(baseline), *extra]
    delayed_fn = virtual_delayed_sync(coverage_ratchet.main, 0.5)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(lambda: delayed_fn(args), budget_ms=50, batch=10)
