"""L38 -- basic functional tests for `checks/backtest.run()` (non-perf).

Split from test_backtest.py to keep perf-parallel suite lean.
"""

from __future__ import annotations

from decimal import Decimal

from src.foundation.backtest.domain.models import CostModel
from src.foundation.validation.checks.backtest import run
from src.foundation.validation.domain.models import Outcome
from tests.foundation.unit.validation.checks.test_backtest import (
    _bars,
    _ctx,
)

_PAID_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))


def test_pass_with_paid_cost_model_and_no_trades() -> None:
    """Basic pass: paid cost model, no trades (never-fires FSM)."""
    bars = _bars(30)
    ctx = _ctx(bars=bars)
    result = run(ctx)
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []
    assert result.check_type == "backtest"
    # bars[0].close=100 -> bars[-1].close=129: (129-100)/100*100 = 29%.
    assert result.metrics["benchmark_buy_hold_return_pct"] == Decimal("29")
