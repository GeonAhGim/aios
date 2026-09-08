"""BT-15b — `vector/fills.py` <-> 이벤트 엔진(`quick_backtest`) 동등성(순수, DB 없음).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(2/2). DoD: (a) `fills.py`는 BT-2~6 산식을 재구현하지 않는다(grep 단언).
(b) 같은 config·시그널·캔들에서 벡터 경로와 이벤트 경로의 체결 시퀀스·최종
equity가 소수 8자리까지(사실은 정확히) 일치한다 — 부분체결 경계 1건, 매도
슬리피지 부호 반전 1건 포함. (c) 벡터 경로가 t+1 봉(캔들·신호 모두)을 체결
판정에 쓰면 실패하는 반증 테스트.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
    run_quick_backtest,
)
from src.foundation.backtest.domain.fill.slippage import apply_slippage
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector import fills as fills_module
from src.foundation.backtest.vector.fills import (
    VectorFillsError,
    VectorSignal,
    _VectorSignalSource,
    run_vector_backtest,
)
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CASH = Decimal("100000")
_D = Decimal


def _columns(
    closes: list[str], *, volume: str = "1000", opens: list[str] | None = None
) -> CandleColumns:
    c = [_D(x) for x in closes]
    o = [_D(x) for x in opens] if opens else [c[0], *c[:-1]] if c else []
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(len(c))], open=o,
        high=[max(a, b) + 1 for a, b in zip(o, c, strict=True)],
        low=[min(a, b) - 1 for a, b in zip(o, c, strict=True)], close=c,
        volume=[_D(volume)] * len(c), quote_volume=[None] * len(c),
    )


def _config(**overrides: Any) -> BacktestConfigV2:
    base: dict[str, Any] = dict(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0, partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None, costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False), calendar="24x7",
    )
    base.update(overrides)
    return BacktestConfigV2(**base)


def _bool_signal(n: int, true_at: set[int]) -> BoolSignal:
    values = np.array([i in true_at for i in range(n)], dtype=np.bool_)
    return BoolSignal(values=values, na=np.zeros(n, dtype=np.bool_))


class _Scripted:
    """`quick_backtest` 테스트와 같은 스타일의 스크립트 전략 — 벡터 경로가
    같은 신호에서 같은 결정을 내는지 대조하는 기준선(이벤트 경로)."""

    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


_CLOSES = ["100", "101", "102", "103", "104", "105", "106", "107"]


# ==== (b) 벡터 경로 <-> 이벤트 경로 동등성 ====


def test_vector_matches_event_on_full_round_trip_with_sell_slippage_sign_flip() -> None:
    """진입(매수)·청산(매도) 왕복 — 매도 체결가는 BT-2 슬리피지 부호가
    매수와 반대로 적용돼야 한다(`apply_slippage`의 `direction`)."""
    cfg, cols = _config(), _columns(_CLOSES)
    qty = _D("10")
    signal = VectorSignal(
        entries=_bool_signal(len(cols), {1}), exits=_bool_signal(len(cols), {4}), quantity=qty,
    )
    vector_result = run_vector_backtest(
        cfg, cols, signal, timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    event_plan = {
        1: OrderIntent(side=OrderSide.BUY, quantity=qty),
        4: OrderIntent(side=OrderSide.SELL, quantity=qty),
    }
    event_result = run_quick_backtest(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted(event_plan), initial_cash=_CASH,
    )

    assert vector_result.fills == event_result.fills
    assert vector_result.equity_curve == event_result.equity_curve
    assert vector_result.final_equity == event_result.final_equity
    assert round(vector_result.final_equity, 8) == round(event_result.final_equity, 8)

    entry, exit_ = vector_result.fills
    assert entry.side == OrderSide.BUY and exit_.side == OrderSide.SELL
    assert entry.price == apply_slippage(
        cfg.slippage, side=OrderSide.BUY, reference_price=cols.open[2], quantity=qty,
        bar_volume=cols.volume[2],
    )
    assert exit_.price == apply_slippage(
        cfg.slippage, side=OrderSide.SELL, reference_price=cols.open[5], quantity=qty,
        bar_volume=cols.volume[5],
    )
    # 매수는 기준가보다 비싸게, 매도는 싸게 — 슬리피지 부호가 실제로 반전됐다.
    assert entry.price > cols.open[2]
    assert exit_.price < cols.open[5]


def test_vector_matches_event_at_partial_fill_boundary() -> None:
    """주문 수량(8) > 봉 거래량 한도(volume=10 * 0.5=5) — 부분체결 경계."""
    cfg = _config(partial_fill=PartialFillConfig(max_participation_pct=_D("0.5")))
    cols = _columns(_CLOSES, volume="10")
    qty = _D("8")
    signal = VectorSignal(
        entries=_bool_signal(len(cols), {2}), exits=_bool_signal(len(cols), set()), quantity=qty,
    )
    vector_result = run_vector_backtest(
        cfg, cols, signal, timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    event_result = run_quick_backtest(
        cfg, cols, timeframe=Timeframe.M1,
        strategy=_Scripted({2: OrderIntent(side=OrderSide.BUY, quantity=qty)}),
        initial_cash=_CASH,
    )

    assert [(f.bar_index, f.quantity, f.remaining_quantity) for f in vector_result.fills] == [
        (3, _D("5"), _D("3")), (4, _D("3"), _D("0")),
    ]
    assert vector_result.fills == event_result.fills
    assert vector_result.final_equity == event_result.final_equity


# ==== (c) 미래참조 금지 반증 테스트 ====


def test_future_candle_values_after_execution_do_not_change_earlier_fill() -> None:
    """체결이 실행된 바로 다음 봉을 극단값으로 오염시켜도 이미 확정된 체결은
    불변이다 — 벡터 경로가 체결 판정에 t+1 봉을 읽으면 이 테스트가 깨진다."""
    cfg = _config()
    baseline = _columns(_CLOSES)
    poisoned = CandleColumns(
        ts=baseline.ts, open=list(baseline.open), high=list(baseline.high),
        low=list(baseline.low), close=list(baseline.close), volume=list(baseline.volume),
        quote_volume=list(baseline.quote_volume),
    )
    # entries[1]=True -> 봉1에서 제출 -> 봉2에서 체결(BT-4 엄격 초과). 봉3(체결
    # 다음 봉)을 극단값으로 오염시킨다.
    poisoned.open[3] = poisoned.high[3] = poisoned.low[3] = poisoned.close[3] = _D("999999")
    poisoned.volume[3] = _D("0.0000001")

    signal = VectorSignal(
        entries=_bool_signal(len(baseline), {1}), exits=_bool_signal(len(baseline), set()),
        quantity=_D("10"),
    )
    baseline_result = run_vector_backtest(
        cfg, baseline, signal, timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    poisoned_result = run_vector_backtest(
        cfg, poisoned, signal, timeframe=Timeframe.M1, initial_cash=_CASH,
    )

    assert len(baseline_result.fills) == len(poisoned_result.fills) == 1
    assert baseline_result.fills[0] == poisoned_result.fills[0]
    assert baseline_result.fills[0].bar_index == 2


def test_signal_indexing_never_reads_next_bar_signal_value() -> None:
    """`entries[i+1]=True`가 `entries[i]=False`인 봉의 판정에 새어 들어오면
    안 된다 — `_VectorSignalSource`를 직접 호출해 확인한다."""
    cols = _columns(["100", "101", "102"])
    signal = VectorSignal(
        entries=_bool_signal(3, {1}), exits=_bool_signal(3, set()), quantity=_D("1"),
    )
    source = _VectorSignalSource(signal)
    zero_position = PositionState(quantity=_D("0"), cash=_D("0"), has_pending_order=False)

    intent_at_0 = source.on_bar(BarWindow(cols, 1), zero_position)
    assert intent_at_0 is None  # entries[0]=False — entries[1]=True를 미리 읽지 않는다

    intent_at_1 = source.on_bar(BarWindow(cols, 2), zero_position)
    assert intent_at_1 is not None and intent_at_1.side == OrderSide.BUY


# ==== negative ====


def test_signal_length_mismatch_is_rejected() -> None:
    cols = _columns(_CLOSES)
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=_bool_signal(len(cols), set()), exits=_bool_signal(len(cols) - 1, set()),
            quantity=_D("1"),
        )
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=_bool_signal(len(cols), set()), exits=_bool_signal(len(cols), set()),
            quantity=_D("0"),
        )
    short_signal = VectorSignal(
        entries=_bool_signal(len(cols) - 1, set()), exits=_bool_signal(len(cols) - 1, set()),
        quantity=_D("1"),
    )
    with pytest.raises(VectorFillsError):
        run_vector_backtest(
            _config(), cols, short_signal, timeframe=Timeframe.M1, initial_cash=_CASH,
        )


# ==== (a) 산식 재구현 0건 — grep 단언 ====


def test_fills_module_does_not_reimplement_bt2_bt3_formulas() -> None:
    """`vector/fills.py` 소스에 BT-2(슬리피지)·BT-3(수수료) 산식·상수가
    직접 나타나면 안 된다 — 이 모듈은 반드시 기존 함수를 호출만 해야 한다."""
    source = inspect.getsource(fills_module)
    forbidden = ("_BPS", "10000", "maker_bps", "taker_bps", "participation_cap", "min_fee")
    for token in forbidden:
        assert token not in source, f"fills.py가 BT-2/BT-3 산식·상수를 재구현한 흔적: {token!r}"
