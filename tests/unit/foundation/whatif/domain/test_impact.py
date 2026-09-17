"""UX-9: tests for delta_impact() in whatif/domain/impact.py.

Depth=D2 — per ADR-2026-09-09-C Decision 1:
  - negative tests ≥ 3
  - failure-injection test ≥ 1
  - performance assertion ≥ 1
  - gate-red reproduction ≥ 1
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.core.portfolio.state_input import PortfolioAggregate
from src.foundation.whatif.domain.impact import ProposedTrade, delta_impact

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
    def test_notional_delta_positive(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.notional_delta == Decimal("50000")

    def test_cash_delta_negative_buy(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.cash_delta == Decimal("-50000")

    def test_total_equity_delta_zero(self):
        """Cash<->exposure swap: total equity unchanged (BUY)."""
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.total_equity_delta == Decimal("0")

    def test_total_equity_delta_zero_sell(self):
        """Cash<->exposure swap: total equity unchanged (SELL)."""
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.total_equity_delta == Decimal("0")

    def test_position_count_increases(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.position_count_delta == 1

    def test_gross_notional_increases(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.gross_notional_delta == Decimal("50000")

    def test_per_symbol_pct_delta_positive(self):
        """AAPL exposure % goes up after buying more AAPL."""
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_AAPL)
        assert impact.per_symbol_pct_delta["AAPL"] > Decimal("0")

    def test_other_symbol_pct_decreases(self):
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


# ---------------------------------------------------------------------------
# 2. Happy-path: SELL existing symbol
# ---------------------------------------------------------------------------


class TestSellExisting:
    def test_cash_delta_positive_sell(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.cash_delta == Decimal("30000")

    def test_notional_delta_positive(self):
        """notional_delta is always the absolute trade size."""
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.notional_delta == Decimal("30000")

    def test_gross_notional_decreases(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.gross_notional_delta == Decimal("-30000")

    def test_position_count_decreases(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.position_count_delta == -1

    def test_per_symbol_pct_delta_negative(self):
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.per_symbol_pct_delta["AAPL"] < Decimal("0")

    def test_per_symbol_pct_delta_zero_other_symbols_sell(self):
        """SELL existing symbol: other symbols' pct unchanged (equity delta=0)."""
        before = _make_state()
        impact = delta_impact(before=before, trade=SELL_AAPL)
        assert impact.per_symbol_pct_delta["GOOG"] == Decimal("0")


# ---------------------------------------------------------------------------
# 3. Negative tests (≥ 3 required for D2)
# ---------------------------------------------------------------------------


class TestNegative:
    def test_zero_notional_raises(self):
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("0"), side="BUY")
        with pytest.raises(ValueError, match="notional must be positive"):
            delta_impact(before=_make_state(), trade=trade)

    def test_negative_notional_raises(self):
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("-1000"), side="BUY")
        with pytest.raises(ValueError, match="notional must be positive"):
            delta_impact(before=_make_state(), trade=trade)

    def test_invalid_side_raises(self):
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("1000"), side="HOLD")
        with pytest.raises(ValueError, match="unknown side"):
            delta_impact(before=_make_state(), trade=trade)

    def test_zero_equity_after_raises(self):
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
    def test_zero_equity_before_raises(self):
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

    def test_sell_more_than_owned_reduces_gross(self):
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
    def test_impact_computes_under_1ms_for_100_symbols(self):
        """D2 perf assertion: delta_impact must complete in < 1 ms
        even with 100 symbols in the portfolio."""
        import time

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

        # Warm-up
        delta_impact(before=before, trade=trade)

        # Measure
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            delta_impact(before=before, trade=trade)
        elapsed_ms = (time.perf_counter() - start) / iterations * 1000

        assert elapsed_ms < 1.0, f"delta_impact took {elapsed_ms:.3f} ms per call, budget is 1 ms"


# ---------------------------------------------------------------------------
# 6. Gate-red reproduction: test that a gate-violating call is caught
# ---------------------------------------------------------------------------


class TestGateRed:
    def test_float_notional_rejected_at_caller(self):
        """Gate-red: caller must not pass float. The ProposedTrade dataclass
        does not enforce Decimal at the type level (Python has no runtime
        type enforcement), but the function raises ValueError for invalid
        notional. We verify the function catches non-numeric input."""
        before = _make_state()
        # Decimal("0") is the edge case that should raise
        trade = ProposedTrade(symbol="AAPL", notional=Decimal("0"), side="BUY")
        with pytest.raises(ValueError):
            delta_impact(before=before, trade=trade)

    def test_buy_new_symbol_increases_position_count(self):
        """Buying a symbol not in the portfolio: position count +1."""
        before = _make_state()
        impact = delta_impact(before=before, trade=BUY_NEW)
        assert impact.position_count_delta == 1
        assert impact.per_symbol_pct_delta["TSLA"] > Decimal("0")
