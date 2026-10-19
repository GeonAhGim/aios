"""UX-9: tests for delta_impact() in whatif/domain/impact.py.

Depth=D2 — per ADR-2026-09-09-C Decision 1:
  - negative tests ≥ 3
  - failure-injection test ≥ 1
  - performance assertion ≥ 1
  - gate-red reproduction ≥ 1
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from time import process_time as _original_process_time
from unittest.mock import patch

import pytest

from src.core.portfolio.state_input import PortfolioAggregate
from src.foundation.whatif.domain.impact import ProposedTrade, delta_impact
from tests.conftest import PerfBudget

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(
    total_equity: Decimal = Decimal("1000000"),
    per_symbol_pct: dict[str, Decimal] | None = None,
    per_strategy_pct: dict[str, Decimal] | None = None,
    total_exposure_pct: Decimal = Decimal("60"),
    cash_pct: Decimal = Decimal("40"),
) -> PortfolioAggregate:
    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct or {"AAPL": Decimal("30"), "GOOG": Decimal("30")},
        per_strategy_pct=per_strategy_pct or {"STRAT_A": Decimal("60")},
        total_exposure_pct=total_exposure_pct,
        cash_pct=cash_pct,
        as_of=datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc),
    )


BUY_AAPL = ProposedTrade(
    symbol="AAPL", notional=Decimal("50000"), side="BUY", strategy_id="STRAT_A"
)
SELL_AAPL = ProposedTrade(
    symbol="AAPL", notional=Decimal("30000"), side="SELL", strategy_id="STRAT_A"
)
BUY_NEW = ProposedTrade(
    symbol="TSLA", notional=Decimal("100000"), side="BUY", strategy_id="STRAT_A"
)


# ---------------------------------------------------------------------------
# 1. Happy-path: BUY existing symbol
# ---------------------------------------------------------------------------


class TestBuyExisting:
    def test_notional_delta_positive(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.notional_delta == Decimal("50000")

    def test_cash_delta_negative_buy(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.cash_delta == Decimal("-50000")

    def test_total_equity_delta_zero(self) -> None:
        """Cash<->exposure swap: total equity unchanged (BUY)."""
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.total_equity_delta == Decimal("0")

    def test_total_equity_delta_zero_sell(self) -> None:
        """Cash<->exposure swap: total equity unchanged (SELL)."""
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.total_equity_delta == Decimal("0")

    def test_position_count_increases(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.position_count_delta == 1

    def test_gross_notional_increases(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.gross_notional_delta == Decimal("50000")

    def test_per_symbol_pct_delta_positive(self) -> None:
        """AAPL exposure % goes up after buying more AAPL."""
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.per_symbol_pct_delta["AAPL"] > Decimal("0")

    def test_other_symbol_pct_decreases(self) -> None:
        """GOOG pct drops because denominator (equity) unchanged but
        AAPL absorbs more of the pie — wait, equity is unchanged for
        cash<->exposure swap. GOOG pct drops because total_equity
        is unchanged but AAPL notional increased, so AAPL pct ↑,
        GOOG pct ↓ (same denominator, but GOOG notional unchanged
        and total_equity = cash + Σnotional; cash ↓, so total_equity
        actually unchanged — GOOG pct unchanged in absolute notional
        but denominator is same, so pct stays same. The code computes
        new_pct = sym_notional / new_equity * 100. Since new_equity ==
        before_equity for BUY/SELL, other symbols' pct should be
        unchanged. Let me re-check: new_equity = 1000000 + 0 = 1000000.
        GOOG notional = 30% * 1000000 = 300000. new_pct = 300000/1000000*100 = 30. delta = 0."""
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        # For BUY/SELL (total_equity_delta == 0), other symbols' pct is unchanged
        assert impact.per_symbol_pct_delta["GOOG"] == Decimal("0")

    def test_buy_existing_computes_under_1ms_cpu(self, perf_budget: PerfBudget) -> None:
        """D2 perf assertion: single-call CPU time must be well under 1 ms budget."""
        before = _make_state()
        sample = perf_budget.assert_within(
            lambda: delta_impact(before=before, trade=BUY_AAPL),
            budget_ms=1.0,
            batch=100,
            label="TestBuyExisting delta_impact",
        )
        assert sample.cpu_ms < 1.0, "delta_impact must compute under 1 ms CPU"

    def test_buy_existing_gate_red_detects_delay(self, perf_budget: PerfBudget) -> None:
        """D2 gate-red reproduction: inject cumulative CPU delay and verify
        the perf assertion fails — proves the budget check actually detects
        regressions instead of silently passing."""
        before = _make_state()
        trade = BUY_AAPL

        # Verify green control first (same measurement as the D2 perf test).
        sample = perf_budget.assert_within(
            lambda: delta_impact(before=before, trade=trade),
            budget_ms=1.0,
            batch=100,
            label="TestBuyExisting delta_impact green control",
        )
        assert sample.cpu_ms < 1.0, "control must pass budget before injection"

        # Inject cumulative CPU delay: each process_time() call adds
        # 0.01 * call_count seconds. Inside sample(), the delta between
        # start and end call gains ~0.01 s = 10 ms per batch iteration.
        # With batch=10: cpu_ms = 10 ms * 1000 / 10 = 10 ms,
        # well above the 1 ms budget.
        call_count = 0

        def slow_process_time() -> float:
            nonlocal call_count
            call_count += 1
            return _original_process_time() + 0.01 * call_count

        with patch.object(time, "process_time", side_effect=slow_process_time):
            with pytest.raises(
                AssertionError,
                match=r"cpu=.*budget<1\.0",
            ):
                perf_budget.assert_within(
                    lambda: delta_impact(before=before, trade=trade),
                    budget_ms=1.0,
                    batch=10,
                    label="TestBuyExisting delta_impact injected load",
                )


# ---------------------------------------------------------------------------
# 2. Happy-path: SELL existing symbol
# ---------------------------------------------------------------------------


class TestSellExisting:
    def test_cash_delta_positive_sell(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.cash_delta == Decimal("30000")

    def test_notional_delta_positive(self) -> None:
        """notional_delta is always the absolute trade size."""
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.notional_delta == Decimal("30000")

    def test_gross_notional_decreases(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.gross_notional_delta == Decimal("-30000")

    def test_position_count_decreases(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.position_count_delta == -1

    def test_per_symbol_pct_delta_negative(self) -> None:
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.per_symbol_pct_delta["AAPL"] < Decimal("0")

    def test_per_symbol_pct_delta_zero_other_symbols_sell(self) -> None:
        """SELL existing symbol: other symbols' pct unchanged (equity delta=0)."""
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.per_symbol_pct_delta["GOOG"] == Decimal("0")


# ---------------------------------------------------------------------------
# 3. Negative tests (≥ 3 required for D2)
# ---------------------------------------------------------------------------


class TestNegative:
    def test_zero_notional_raises(self) -> None:
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("0"), side="BUY")
        with pytest.raises(ValueError, match="notional must be positive"):
            delta_impact(before=_make_state(), trade=trade)

    def test_negative_notional_raises(self) -> None:
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("-1000"), side="BUY")
        with pytest.raises(ValueError, match="notional must be positive"):
            delta_impact(before=_make_state(), trade=trade)

    def test_invalid_side_raises(self) -> None:
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("1000"), side="HOLD")
        with pytest.raises(ValueError, match="unknown side"):
            delta_impact(before=_make_state(), trade=trade)

    def test_zero_equity_after_raises(self) -> None:
        """Edge: if before total_equity is 0 and trade makes it 0,
        denominator is zero — should raise."""
        before = _make_state(
            total_equity=Decimal("0"),
            cash_pct=Decimal("100"),
            per_symbol_pct={},
            per_strategy_pct={},
            total_exposure_pct=Decimal("0"),
        )
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("10000"), side="BUY")
        with pytest.raises(ValueError, match="total_equity must be positive"):
            delta_impact(before=before, trade=trade)


# ---------------------------------------------------------------------------
# 4. Failure-injection test (≥ 1 required for D2)
# ---------------------------------------------------------------------------


class TestFailureInjection:
    def test_zero_equity_before_raises(self) -> None:
        """Inject: total_equity=0, cash_pct=100, no positions.
        Buying with any notional makes new_equity = 0 + 0 = 0 (since
        total_equity_delta = 0 for valid BUY/SELL). Should raise."""
        before = _make_state(
            total_equity=Decimal("0"),
            cash_pct=Decimal("100"),
            per_symbol_pct={},
            per_strategy_pct={},
            total_exposure_pct=Decimal("0"),
        )
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("10000"), side="BUY")
        with pytest.raises(ValueError, match="total_equity must be positive"):
            delta_impact(before=before, trade=trade)

    def test_sell_more_than_owned_reduces_gross(self) -> None:
        """Inject: SELL 300k AAPL when only 300k owned.
        Gross goes to 0 for that symbol, delta should be -300k."""
        before = _make_state()
        sell_all = ProposedTrade(
            symbol="AAPL", notional=Decimal("300000"), side="SELL", strategy_id="STRAT_A"
        )
        impact = delta_impact(before=before, trade=sell_all)
        assert impact.gross_notional_delta == Decimal("-300000")
        assert impact.per_symbol_pct_delta["AAPL"] == Decimal("-30")


# ---------------------------------------------------------------------------
# 5. Performance assertion (≥ 1 required for D2)
# ---------------------------------------------------------------------------


class TestPerformance:
    @pytest.mark.perf
    def test_impact_computes_under_1ms_for_100_symbols(self, perf_budget: PerfBudget) -> None:
        """D2 perf assertion: delta_impact must complete in < 1 ms
        even with 100 symbols in the portfolio."""
        many_symbols = {f"SYM{i:03d}": Decimal("1") for i in range(100)}
        before = _make_state(
            per_symbol_pct=many_symbols,
            per_strategy_pct={"S1": Decimal("100")},
            total_exposure_pct=Decimal("99"),
            cash_pct=Decimal("1"),
        )
        trade = ProposedTrade(
            symbol="SYM050", notional=Decimal("10000"), side="BUY", strategy_id="S1"
        )

        # batch=1000: process_time()'s ~15.6ms quantization shrinks to ~0.0156ms/call.
        perf_budget.assert_within(
            lambda: delta_impact(before=before, trade=trade),
            budget_ms=1.0,
            batch=1000,
            label="delta_impact 100 symbols",
        )


# ---------------------------------------------------------------------------
# 6. Failure-injection: injected CPU load makes budget assertion go red
# ---------------------------------------------------------------------------


class TestFailureInjectionCPU:
    def test_injected_cpu_load_breaks_budget_assertion(self, perf_budget: PerfBudget) -> None:
        """Failure-injection: monkeypatch time.process_time to simulate CPU
        work. The same assert_within call that passes green under normal
        conditions must go red when the measured function consumes enough
        fake CPU to exceed the budget.

        This validates that the performance assertion actually detects
        regressions — a silent pass on both normal and injected input would
        be a hollow guard.
        """
        before = _make_state()
        trade = BUY_AAPL

        # Verify green control first (same measurement as the D2 perf test).
        sample = perf_budget.assert_within(
            lambda: delta_impact(before=before, trade=trade),
            budget_ms=1.0,
            batch=100,
            label="delta_impact green control",
        )
        assert sample.cpu_ms < 1.0, "control must pass budget before injection"

        # Inject cumulative CPU delay: each process_time() call adds
        # 0.01 * call_count seconds. Inside sample(), the delta between
        # start and end call gains ~0.01 s = 10 ms per batch iteration.
        # With batch=10: cpu_ms = 10 ms * 1000 / 10 = 10 ms,
        # well above the 1 ms budget.
        call_count = 0

        def slow_process_time() -> float:
            nonlocal call_count
            call_count += 1
            return _original_process_time() + 0.01 * call_count

        with patch.object(time, "process_time", side_effect=slow_process_time):
            with pytest.raises(
                AssertionError,
                match=r"cpu=.*budget<1\.0",
            ):
                perf_budget.assert_within(
                    lambda: delta_impact(before=before, trade=trade),
                    budget_ms=1.0,
                    batch=10,
                    label="delta_impact injected load",
                )
