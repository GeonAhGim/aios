"""L27 -- BarFillSimulator port compliance + numeric parity with the
pre-L27 application/simulate_fill.py implementation (origin/main 486dadb9).
"""

from __future__ import annotations

import ast
import inspect
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.check_code_language import ROOT as LANGUAGE_GATE_ROOT
from scripts.check_code_language import count_file
from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.adapters import bar_fill_simulator as bar_fill_simulator_module
from src.foundation.backtest.adapters.bar_fill_simulator import BarFillSimulator
from src.foundation.backtest.application import simulate_fill as simulate_fill_module
from src.foundation.backtest.application.simulate_fill import simulate_fill
from src.foundation.backtest.domain.models import CostModel
from src.foundation.backtest.domain.rules import (
    LookaheadViolationError,
    assert_fill_after_signal,
)
from src.foundation.backtest.ports.fill_simulator import FillSimulatorPort

_NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _bar(*, open_price: str = "100") -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=Decimal(open_price),
        low=Decimal(open_price),
        close=Decimal(open_price),
        volume=Decimal("1"),
        open_time=_NOW,
        close_time=_NOW,
    )


def test_bar_fill_simulator_satisfies_port() -> None:
    assert isinstance(BarFillSimulator(), FillSimulatorPort) is True


def test_numeric_parity_buy_quantity_one() -> None:
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    fill = BarFillSimulator().simulate(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=cost_model,
        seed=0,
    )
    assert fill.price == Decimal("100.1")
    assert fill.slippage_cost == Decimal("0.1")
    assert fill.fee == Decimal("0.050050")


def test_numeric_parity_sell_quantity_two() -> None:
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    fill = BarFillSimulator().simulate(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.SELL,
        quantity=Decimal("2"),
        cost_model=cost_model,
        seed=0,
    )
    assert fill.price == Decimal("99.9")
    assert fill.slippage_cost == Decimal("0.2")
    assert fill.fee == Decimal("0.0999")


def test_numeric_parity_buy_quantity_three_via_wrapper() -> None:
    cost_model = CostModel(fee_bps=Decimal("20"), slippage_bps=Decimal("50"))
    fill = simulate_fill(
        bar=_bar(open_price="200"),
        bar_index=4,
        side=OrderSide.BUY,
        quantity=Decimal("3"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("201.0")
    assert fill.slippage_cost == Decimal("3.0")
    assert fill.fee == Decimal("1.2060")


def test_application_wrapper_no_longer_holds_fill_arithmetic() -> None:
    source = inspect.getsource(simulate_fill_module)
    assert "10000" not in source
    assert "slippage_direction" not in source
    assert "_BPS" not in source
    assert "BarFillSimulator" in source


def test_look_ahead_violation_rejected_for_same_bar_index() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    with pytest.raises(LookaheadViolationError):
        BarFillSimulator().simulate(
            bar=_bar(),
            bar_index=5,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
            seed=0,
            signal_bar_index=5,
        )


def test_deterministic_across_seeds() -> None:
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    bar = _bar(open_price="123.45")
    fill_seed_1 = BarFillSimulator().simulate(
        bar=bar,
        bar_index=7,
        side=OrderSide.SELL,
        quantity=Decimal("1.5"),
        cost_model=cost_model,
        seed=1,
    )
    fill_seed_2 = BarFillSimulator().simulate(
        bar=bar,
        bar_index=7,
        side=OrderSide.SELL,
        quantity=Decimal("1.5"),
        cost_model=cost_model,
        seed=2,
    )
    assert fill_seed_1 == fill_seed_2


def test_no_random_or_time_imports_in_adapter_or_wrapper() -> None:
    for module in (bar_fill_simulator_module, simulate_fill_module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name not in {"random", "time"} for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"random", "time"}


def test_rejects_non_positive_quantity() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    with pytest.raises(ValueError, match="quantity"):
        BarFillSimulator().simulate(
            bar=_bar(),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            cost_model=cost_model,
            seed=0,
        )


def test_rejects_negative_slippage_bps() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("-1"))
    with pytest.raises(ValueError, match="slippage_bps"):
        BarFillSimulator().simulate(
            bar=_bar(),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
            seed=0,
        )


# -- D2 failure injection -----------------------------------------------------


def test_wrapper_fill_bar_index_caught_by_domain_lookahead_rule_on_regression() -> None:
    """`application/simulate_fill.py`'s wrapper signature has no
    `signal_bar_index` parameter of its own -- the real caller
    (`run_backtest.py`'s replay loop) is the one responsible for keeping the
    triggering signal's bar_index and the fill's bar_index apart. This
    reproduces the regression where a future caller (that loop, or a PAPER
    `FillSimulatorPort` implementer) wires the same bar_index for both --
    e.g. an off-by-one that fills within the signal bar instead of the bar
    after -- and proves I-05's actual downstream guard
    (`domain.rules.assert_fill_after_signal`) still rejects it using this
    wrapper's real `SimulatedFill` output, not a hand-built stand-in."""
    fill = simulate_fill(
        bar=_bar(),
        bar_index=5,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
    )

    class _RegressedSignalEvent:
        bar_index = 5  # same bar as the fill above -- the injected regression

    with pytest.raises(LookaheadViolationError):
        assert_fill_after_signal(_RegressedSignalEvent(), fill)


# -- D2 performance assertion --------------------------------------------------


@pytest.mark.perf
def test_bar_fill_simulator_throughput_budget() -> None:
    """`BarFillSimulator.simulate()` runs once per pending-order fill on
    every bar of the replay loop (`run_backtest.py`); 10,000 calls must stay
    well under a 300ms budget to rule out an accidental O(n) loop or Decimal
    context regression creeping into this hot path."""
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    bar = _bar(open_price="100")
    simulator = BarFillSimulator()
    start = time.perf_counter()
    for i in range(10_000):
        simulator.simulate(
            bar=bar,
            bar_index=i,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
            seed=0,
        )
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"10,000 simulate() calls took {elapsed * 1000:.2f}ms, budget 300ms"


# -- D2 gate-red repro ---------------------------------------------------------


def test_check_code_language_gate_flags_synthetic_korean_comment_regression(
    tmp_path: Path,
) -> None:
    """Comments/docstrings under `src/` must be English (ADR-2026-09-07-A,
    `scripts/check_code_language.py`'s Hangul ratchet). Proves the gate's
    real detector (`count_file`) fires on a synthetic Korean-comment
    regression, and that this leaf's actual files stay green -- rather than
    trusting that "no Korean in the diff" holds without ever exercising the
    scanner that enforces it."""
    regressed = tmp_path / "regressed_bar_fill_simulator.py"
    regressed.write_text(
        "def simulate() -> None:\n    # 잘못된 한글 주석 -- 회귀 시나리오\n    pass\n",
        encoding="utf-8",
    )
    assert count_file(regressed) == 1

    assert (
        count_file(LANGUAGE_GATE_ROOT / "src/foundation/backtest/adapters/bar_fill_simulator.py")
        == 0
    )
    assert (
        count_file(LANGUAGE_GATE_ROOT / "src/foundation/backtest/application/simulate_fill.py") == 0
    )
