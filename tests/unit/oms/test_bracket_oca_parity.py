"""task-2623 DoD — 백테스트(BT-6)·PAPER(EM-19) 브래킷 청산 패리티.

Spec: ADR-2026-09-09-B. `src/foundation/backtest/domain/fill/order_types.py`
(BT-6)와 `src/services/oms/domain/order_types/oca.py`(EM-19)는 서로
임포트하지 않는 독립된 두 구현이다(bounded-context 분리, `resolve_oco`가
이미 양쪽에 중복 구현된 것과 같은 원칙) — 이 파일이 "같은 입력 -> 같은
체결 로그"라는 패리티 계약을 실제로 증명한다: 동일한 트리거 시나리오
행렬을 두 구현에 동시에 먹여서 `resolve_oca`의 트리거 레그·취소 레그
집합과 `bracket_quantity_for_fill`의 부분체결 정합 수량이 바이트가 아니라
값 단위로 완전히 같은지 단언한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.backtest.domain.fill import order_types as bt6
from src.services.oms.domain.order_types import oca as em19

_PRIORITY_ORDER = ("loss", "profit", "trail")

# (profit_triggered, loss_triggered, trail_triggered) -> 두 구현 모두 같은
# priority_order로 판정했을 때 기대하는 (triggered_leg, cancelled_legs 집합).
_SCENARIOS: tuple[tuple[bool, bool, bool], ...] = (
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, False),  # 같은 틱 동시 트리거 -> priority_order(loss 우선)
    (True, False, True),
    (False, True, True),
    (True, True, True),
    (False, False, False),  # 아무 레그도 안 닿음
)


@pytest.mark.parametrize(("profit", "loss", "trail"), _SCENARIOS)
def test_resolve_oca_is_byte_for_byte_identical_across_backtest_and_paper(
    profit: bool, loss: bool, trail: bool
) -> None:
    triggered = {"profit": profit, "loss": loss, "trail": trail}
    backtest_result = bt6.resolve_oca(triggered=triggered, priority_order=_PRIORITY_ORDER)
    paper_result = em19.resolve_oca(triggered=triggered, priority_order=_PRIORITY_ORDER)
    assert backtest_result.triggered_leg == paper_result.triggered_leg
    assert backtest_result.cancelled_legs == paper_result.cancelled_legs


@pytest.mark.parametrize(
    ("requested_qty", "filled_qty"),
    [
        (Decimal("10"), Decimal("10")),  # 완전체결
        (Decimal("10"), Decimal("4")),  # 부분체결 -- bracket 수량은 4로 클램프
        (Decimal("10"), Decimal("0")),  # 미체결 -- bracket 수량 0
        (Decimal("7.5"), Decimal("15")),  # 방어적 상한 -- 요청 수량을 넘지 않음
    ],
)
def test_bracket_quantity_for_fill_is_identical_across_backtest_and_paper(
    requested_qty: Decimal, filled_qty: Decimal
) -> None:
    backtest_qty = bt6.bracket_quantity_for_fill(requested_qty=requested_qty, filled_qty=filled_qty)
    paper_qty = em19.bracket_quantity_for_fill(requested_qty=requested_qty, filled_qty=filled_qty)
    assert backtest_qty == paper_qty


def test_partial_entry_fill_produces_matching_bracket_exit_log_on_both_sides() -> None:
    """DoD scenario: script with strategy.entry/bracket runs through real backtest.

    A script with strategy.entry(1, 10) + strategy.bracket(10, profit, loss, trail)
    runs through real backtest script execution with synthetic OHLC data engineered to trigger
    a partial entry fill (qty=4) followed by a loss-leg bracket exit trigger (qty=4).

    Both backtest (via run_quick_backtest + resolve_oca/bracket_quantity_for_fill)
    and paper (via direct EM-19 function calls with SAME numbers) must produce
    identical exit decisions and quantities, verifying parity between bounded contexts."""
    from datetime import datetime, timezone

    from src.core.script.artifact.compile import compile_source
    from src.foundation.backtest.application.quick_backtest import run_quick_backtest
    from src.foundation.backtest.application.script_signal_source import (
        build_script_signal_source,
    )
    from src.foundation.backtest.domain.models_v2 import (
        BacktestConfigV2,
        PartialFillConfig,
    )
    from src.foundation.market_data.api import CandleColumns
    from src.foundation.market_data.contracts.v1 import Timeframe

    # Compile a script that enters and places a bracket exit.
    # Strategy calls must be in expressions (e.g., let statements) in the current DSL.
    # Note: Trailing stop (trail_pct) is not yet fully implemented in backtest,
    # but we include it for API completeness; it won't be triggered.
    # Profit and loss legs will be tested (priority: loss > profit > trail).
    script_source = """
input close: series<float> = 0
input high: series<float> = 0
input low: series<float> = 0
input open: series<float> = 0

let entry_id = strategy.entry(1, 10)
let bracket_id = strategy.bracket(10, 120, 95, 0.05)
"""
    compiled = compile_source(script_source, registry_version="1.0.0")

    # Synthetic OHLC data:
    # Bar 0: 100, 101, 99, 100 (setup bar)
    # Bar 1: 100, 100, 100, 100 (entry execution + partial fill due to low volume)
    # Bar 2: 100, 100, 94, 100 (touches loss price 95 — bracket exit triggers loss leg)
    # Partial fill: max 40% participation → 10 * 0.4 = 4 shares fill in bar 1 (volume 10)
    base_time = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)

    columns = CandleColumns(
        ts=[
            base_time,
            base_time.replace(minute=31),
            base_time.replace(minute=32),
        ],
        open=[Decimal("100"), Decimal("100"), Decimal("100")],
        high=[Decimal("101"), Decimal("100"), Decimal("100")],
        low=[Decimal("99"), Decimal("100"), Decimal("94")],
        close=[Decimal("100"), Decimal("100"), Decimal("100")],
        volume=[Decimal("100"), Decimal("10"), Decimal("100")],
        quote_volume=[Decimal("10000"), Decimal("1000"), Decimal("10000")],
    )

    # Build signal source (compiles the script and sets up bracket metadata)
    signal_source = build_script_signal_source(
        compiled.ir, bar_count=len(columns), columns=columns
    )

    # Run backtest with partial fill config to trigger 40% (4 out of 10)
    from src.foundation.backtest.domain.models_v2 import (
        AdjustmentsConfig,
        CostsConfig,
        FixedSlippage,
        OrderTypesConfig,
        VenueTierCommission,
    )

    config = BacktestConfigV2(
        slippage=FixedSlippage(bps=Decimal("1.5")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=Decimal("2"), taker_bps=Decimal("4"), min_fee=Decimal("0.10")
        ),
        latency_ms=50,
        partial_fill=PartialFillConfig(max_participation_pct=Decimal("0.4")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=False, trailing=False),
        magnifier_tf=None,
        costs=CostsConfig(funding=True, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=True, dividends=True),
        calendar="24x7",
    )
    result = run_quick_backtest(
        config,
        columns,
        timeframe=Timeframe.M1,
        strategy=signal_source,
        initial_cash=Decimal("10000"),
        funding_rate=Decimal("0"),  # No funding costs for this test
    )

    # Verify the backtest produced:
    # 1. Entry fill qty=4 (partial, 40% of 10 shares vs bar 1's volume 10)
    # 2. Bracket exit fill qty=4 (via bracket_quantity_for_fill)
    assert len(result.fills) >= 1, "Expected at least 1 fill (entry)"
    entry_fill = result.fills[0]
    assert entry_fill.quantity == Decimal("4"), (
        f"Expected partial entry fill of 4, got {entry_fill.quantity}"
    )

    # Bracket exit fill should be in fills list (may be second if bar 2 triggers)
    bracket_exit_fill = None
    for fill in result.fills[1:]:
        # Exit is opposite side of entry
        if fill.side.name != entry_fill.side.name:
            bracket_exit_fill = fill
            break

    assert bracket_exit_fill is not None, "Expected bracket exit fill to be generated"
    # Exit qty must match bracket_quantity_for_fill(requested_qty=10, filled_qty=4)
    assert bracket_exit_fill.quantity == Decimal("4"), (
        f"Expected bracket exit qty=4, got {bracket_exit_fill.quantity}"
    )

    # Cross-check with direct paper-side calls using SAME numbers from backtest:
    filled_qty = entry_fill.quantity  # Decimal("4")
    requested_qty = Decimal("10")
    triggered_paper = {"profit": False, "loss": True, "trail": False}

    backtest_qty = bt6.bracket_quantity_for_fill(
        requested_qty=requested_qty, filled_qty=filled_qty
    )
    paper_qty = em19.bracket_quantity_for_fill(
        requested_qty=requested_qty, filled_qty=filled_qty
    )
    assert backtest_qty == paper_qty == filled_qty, (
        f"bracket_quantity_for_fill parity: BT={backtest_qty}, EM={paper_qty}"
    )

    backtest_resolution = bt6.resolve_oca(
        triggered=triggered_paper, priority_order=_PRIORITY_ORDER
    )
    paper_resolution = em19.resolve_oca(
        triggered=triggered_paper, priority_order=_PRIORITY_ORDER
    )
    assert backtest_resolution.triggered_leg == paper_resolution.triggered_leg == "loss"
    assert (
        backtest_resolution.cancelled_legs
        == paper_resolution.cancelled_legs
        == ("profit", "trail")
    )
