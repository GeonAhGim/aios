"""L27 -- BarFillSimulator port compliance + numeric parity with the
pre-L27 application/simulate_fill.py implementation (origin/main 486dadb9).
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

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
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_NOW = datetime(2026, 1, 2, tzinfo=UTC)


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
def test_bar_fill_simulator_throughput_budget(perf_budget: PerfBudget) -> None:
    """`BarFillSimulator.simulate()` runs once per pending-order fill on
    every bar of the replay loop (`run_backtest.py`); 10,000 calls must stay
    well under a 300ms budget to rule out an accidental O(n) loop or Decimal
    context regression creeping into this hot path."""
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    bar = _bar(open_price="100")
    simulator = BarFillSimulator()
    perf_budget.assert_within(
        lambda: _run_fill_batch(simulator, bar, cost_model),
        budget_ms=300,
        batch=1,
    )


def _run_fill_batch(
    simulator: BarFillSimulator,
    bar: Candle,
    cost_model: CostModel,
) -> None:
    """10,000 simulate() calls — batched to amortise process_time quantisation."""
    for i in range(10_000):
        simulator.simulate(
            bar=bar,
            bar_index=i,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
            seed=0,
        )


# -- D2 CPU 측정 0 수렴 + 지연 주입 적색 재현 ----------------------------------


@pytest.mark.perf
def test_simulate_cpu_measurement_converges_with_batch(
    perf_budget: PerfBudget,
) -> None:
    """CPU 측정 0 수렴 검증: 단일 `BarFillSimulator.simulate()` 호출은 Windows
    `time.process_time()` 약 15.6ms 양자화 tick 때문에 batch=1로 측정하면
    항상 0ms로 읽힌다. batch=8로 묶으면 양자화 오차가 호출당 15.6/8 ≈ 2ms로
    줄어 실제 CPU 시간을 측정할 수 있다.

    이 테스트는 batch 매개변수가 양자화 문제를 완화한다는 사실을
    문서화한다(task-11539 감사).
    """
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    bar = _bar(open_price="100")
    simulator = BarFillSimulator()

    def single_call() -> None:
        simulator.simulate(
            bar=bar,
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
            seed=0,
        )

    # best_of(여러 번 중 최소)로 측정해 양자화 tick 1회에 흔들리지 않게 한다
    single_sample = perf_budget.best_of(single_call, n=5, warmup=1, batch=1)
    assert single_sample.cpu_ms < 1.0, (
        f"batch=1 cpu={single_sample.cpu_ms:.3f}ms — "
        f"Windows quantization tick(15.6ms)보다 빠른 호출은 0ms 수렴"
    )

    batch_sample = perf_budget.best_of(single_call, n=5, warmup=1, batch=8)
    assert batch_sample.cpu_ms < 15.6, (
        f"batch=8 cpu={batch_sample.cpu_ms:.3f}ms — 양자화 tick 15.6ms 미만이어야 함"
    )


@pytest.mark.perf
def test_perf_budget_assertion_catches_10x_cpu_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2): 기존 처리량 예산(300ms) 단언이 10배 가상 지연
    (3s)에서 AssertionError로 적색이 되고, 지연 없는 정상 입력에서는 녹색임을
    같은 측정/단언으로 증명한다. 실제 sleep 없이 `virtual_delayed_sync`가
    측정치에 지연을 더한다(task-11539 감사)."""
    budget_ms = 300.0
    cost_model = CostModel(fee_bps=Decimal("5"), slippage_bps=Decimal("10"))
    bar = _bar(open_price="100")
    simulator = BarFillSimulator()

    def run() -> None:
        _run_fill_batch(simulator, bar, cost_model)

    perf_budget.assert_within(run, budget_ms=budget_ms, batch=1, label="normal")

    delayed = virtual_delayed_sync(run, seconds=3.0)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            delayed, budget_ms=budget_ms, batch=1, label="simulate_10x_injected"
        )
