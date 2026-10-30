"""L4_strategy_portfolio_backtest_v1.0.md#L4-07 perf — fixed_fractional.py perf/gate-red tests.

Split from test_fixed_fractional.py (task-21661) to isolate virtual-delay ledger state
from pure unit tests, preventing parallel-CI interference on the shared
``perf_inject._virtual_cpu_ns`` / ``_virtual_wall_ns`` globals.
"""

from __future__ import annotations

import pytest

from src.core.portfolio.sizing.fixed_fractional import size
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync
from tests.unit.core.portfolio.sizing.test_fixed_fractional import _inp

# ---------------------------------------------------------------------------
# Performance assertion — ADR-2026-09-09-C Decision 1 budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_size_performance_under_1ms(perf_budget: PerfBudget) -> None:
    """sizing() is a pure Decimal arithmetic path — p95 < 1 ms real CPU."""
    sample = perf_budget.best_of(lambda: size(_inp()), n=5, warmup=1, batch=100)
    assert sample.cpu_ms < 1.0, perf_budget.describe(sample, budget_ms=1.0)


@pytest.mark.perf
def test_size_budget_fails_with_virtual_delay_injection(perf_budget: PerfBudget) -> None:
    """virtual_delayed_sync으로 예산의 10배 지연을 주입하면 assert_within가 적색이 된다.

    실제 sleep/busy-wait 없이 virtual ledger에 지연만 추가하므로 테스트는 즉시 끝난다.
    PerfBudget가 ledger를 측정치에 합산하므로 같은 검출력을 가진다.
    """
    # 정상 입력은 녹색 — 예산 내
    sample_normal = perf_budget.best_of(lambda: size(_inp()), n=5, warmup=1, batch=100)
    assert sample_normal.cpu_ms < 0.1  # 예산 0.1ms

    # 예산 10배(1ms) 가상 지연 주입 → 적색
    slow_size = virtual_delayed_sync(lambda: size(_inp()), 0.001)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            slow_size,
            budget_ms=0.1,
            n=5,
            warmup=1,
            batch=100,
            label="virtual_delay 10× sizing",
        )


@pytest.mark.perf
def test_size_gate_red_reproduction_via_virtual_delay(perf_budget: PerfBudget) -> None:
    """gate-red 재현(ADR-2026-09-09-C D2): virtual_delayed_sync로 CPU 부하를 주입해
    게이트가 적색으로 전환됨을 확인한다. 실제 busy-wait 없이 측정치만 늘린다.

    전역 가상 지연 레저를 초기화해 선행 테스트의 상태가 본 테스트에 영향을 주지 않게 한다.
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    # 50ms 가상 CPU 지연 — 예산(0.1ms)의 500배
    slow_size = virtual_delayed_sync(lambda: size(_inp()), 0.05)
    sample = perf_budget.sample(slow_size)
    assert sample.cpu_ms >= 50.0  # 가상 지연이 측정치에 반영됨
    # 예산을 훨씬 초과하므로 assert_within가 적색이어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            slow_size,
            budget_ms=0.1,
            n=5,
            warmup=1,
            batch=100,
            label="gate-red 50ms sizing",
        )
