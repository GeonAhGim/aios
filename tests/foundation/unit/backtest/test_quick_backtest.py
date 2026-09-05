"""BT-10 quick_backtest — BT-2~8 위임 확인·결정론·미래 참조 차단·negative(순수, DB 없음).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5 BT-10, §3.4, §9.5.
기대값은 도메인 리프(BT-2~8) 함수를 테스트가 직접 호출해 얻는다 — 엔진이 같은
값을 내면 "재구현이 아니라 위임"이라는 증명이다. 실DB 컬럼 경로·1개월 M1
측정은 tests/foundation/integration/backtest/test_quick_backtest.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application import quick_backtest_fill as qf
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    LookAheadError,
    OrderIntent,
    PositionState,
    QuickBacktestInputError,
    TooManyBarsError,
    run_quick_backtest,
)
from src.foundation.backtest.domain.costs.borrow import compute_borrow_cost
from src.foundation.backtest.domain.fill.commission import compute_commission
from src.foundation.backtest.domain.fill.latency import resolve_execution_bar_index
from src.foundation.backtest.domain.fill.order_types import OrderTypeDisabledError
from src.foundation.backtest.domain.fill.slippage import apply_slippage
from src.foundation.backtest.domain.magnifier import IncompatibleMagnifierTimeframeError
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CASH = Decimal("100000")
_D = Decimal


def _columns(
    closes: list[str], *, volume: str = "1000", opens: list[str] | None = None
) -> CandleColumns:
    """open은 직전 close(첫 봉은 자기 close), high/low는 open·close 바깥 ±1."""
    c = [_D(x) for x in closes]
    o = [_D(x) for x in opens] if opens else [c[0], *c[:-1]] if c else []
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(len(c))], open=o,
        high=[max(a, b) + 1 for a, b in zip(o, c, strict=True)],
        low=[min(a, b) - 1 for a, b in zip(o, c, strict=True)], close=c,
        volume=[_D(volume)] * len(c), quote_volume=[None] * len(c),
    )


def _config(**overrides: object) -> BacktestConfigV2:
    base: dict[str, object] = dict(
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
    return BacktestConfigV2(**base)  # type: ignore[arg-type]


class _Scripted:
    """봉 인덱스(= len(window)-1) → 주문 의도. dict 조회만 하므로 결정론."""

    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan, self.calls = plan, 0

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        self.calls += 1
        return self.plan.get(len(window) - 1)


def _run(config: BacktestConfigV2, columns: CandleColumns, plan: dict[int, OrderIntent], **kw):
    return run_quick_backtest(
        config, columns, timeframe=Timeframe.M1, strategy=_Scripted(plan), initial_cash=_CASH, **kw
    )


_BUY10 = OrderIntent(side=OrderSide.BUY, quantity=_D("10"))
_SELL10 = OrderIntent(side=OrderSide.SELL, quantity=_D("10"))
_CLOSES = ["100", "101", "102", "103", "104", "105", "106", "107"]


def test_market_order_fills_next_bar_with_bt2_bt3_values() -> None:
    cfg, cols = _config(), _columns(_CLOSES)
    result = _run(cfg, cols, {2: _BUY10})

    assert [f.bar_index for f in result.fills] == [3]  # 신호 봉 2 → 체결 봉 3(look-ahead 안전)
    fill = result.fills[0]
    expected_price = apply_slippage(
        cfg.slippage, side=OrderSide.BUY, reference_price=cols.open[3], quantity=_D("10"),
        bar_volume=cols.volume[3],
    )
    assert fill.price == expected_price
    assert fill.commission == compute_commission(
        cfg.commission, is_maker=False, notional=expected_price * _D("10")
    )
    assert fill.remaining_quantity == 0
    assert result.position_quantity == _D("10")
    assert result.cash == _CASH - expected_price * _D("10") - fill.commission
    assert result.final_equity == result.cash + _D("10") * cols.close[-1]
    assert len(result.equity_curve) == result.bars == len(cols)


def test_every_fill_model_is_delegated_not_reimplemented(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, int] = {}
    for name in (
        "apply_slippage", "compute_commission", "resolve_execution_bar_index",
        "compute_partial_fill", "magnify", "compute_funding_cost", "compute_borrow_cost",
        "ensure_order_type_enabled", "is_limit_triggered",
    ):
        original = getattr(qf, name)

        def _wrap(*a, _orig=original, _name=name, **k):
            calls[_name] = calls.get(_name, 0) + 1
            return _orig(*a, **k)

        monkeypatch.setattr(qf, name, _wrap)

    limit_sell = OrderIntent(
        side=OrderSide.SELL, quantity=_D("10"), order_type="limit", trigger_price=_D("104")
    )
    result = _run(_config(costs=CostsConfig(funding=True, borrow_apr=_D("0.1"))),
                  _columns(_CLOSES), {1: _BUY10, 3: limit_sell}, funding_rate=_D("0.0001"))

    assert [f.bar_index for f in result.fills] == [2, 4]
    # 주문 수(2) = 체결 모델 호출 수 — 봉 수(8)와 무관
    assert calls["resolve_execution_bar_index"] == 2 == calls["magnify"]
    assert calls["apply_slippage"] == calls["compute_commission"] == 2
    assert calls["compute_partial_fill"] == 2
    assert calls["ensure_order_type_enabled"] == 1 and calls["is_limit_triggered"] >= 1
    assert calls["compute_funding_cost"] == 1  # 청산 1회 정산
    assert "compute_borrow_cost" not in calls  # 롱 포지션은 차입 없음(숏은 아래 BT-8 테스트)


def test_latency_pushes_execution_bar_per_bt4() -> None:
    cfg, cols = _config(latency_ms=60_000), _columns(_CLOSES)
    result = _run(cfg, cols, {2: _BUY10})
    expected = 3 + resolve_execution_bar_index(
        submitted_at=cols.ts[2], latency_ms=60_000, bar_open_times=cols.ts[3:]
    )
    assert [f.bar_index for f in result.fills] == [expected] == [4]


def test_partial_fill_carries_remaining_to_next_bar() -> None:
    cfg = _config(partial_fill=PartialFillConfig(max_participation_pct=_D("0.5")))
    result = _run(cfg, _columns(_CLOSES, volume="10"), {2: OrderIntent(OrderSide.BUY, _D("8"))})
    assert [(f.bar_index, f.quantity, f.remaining_quantity) for f in result.fills] == [
        (3, _D("5"), _D("3")), (4, _D("3"), _D("0")),
    ]
    assert result.position_quantity == _D("8") and result.expired_orders == 0


def test_limit_order_waits_for_trigger_and_pays_maker_fee() -> None:
    cfg = _config()
    cols = _columns(["100", "100", "100", "100", "100", "89", "100"])  # 봉 5에서 저가 88
    intent = OrderIntent(OrderSide.BUY, _D("1"), order_type="limit", trigger_price=_D("90"))
    result = _run(cfg, cols, {1: intent})
    assert [f.bar_index for f in result.fills] == [5]
    fill = result.fills[0]
    assert fill.price == apply_slippage(
        cfg.slippage, side=OrderSide.BUY, reference_price=_D("90"), quantity=_D("1"),
        bar_volume=cols.volume[5],
    )
    assert fill.commission == compute_commission(cfg.commission, is_maker=True, notional=fill.price)


def test_stop_order_gapping_through_fills_at_bar_open() -> None:
    cols = _columns(["100", "100", "100", "110"], opens=["100", "100", "100", "108"])
    intent = OrderIntent(OrderSide.BUY, _D("1"), order_type="stop", trigger_price=_D("105"))
    result = _run(_config(), cols, {2: intent})
    assert result.fills[0].bar_index == 3
    assert result.fills[0].price == apply_slippage(
        _config().slippage, side=OrderSide.BUY, reference_price=_D("108"), quantity=_D("1"),
        bar_volume=cols.volume[3],
    )


def test_short_borrow_cost_delegates_to_bt8() -> None:
    cfg, cols = _config(costs=CostsConfig(funding=False, borrow_apr=_D("0.1"))), _columns(_CLOSES)
    result = _run(cfg, cols, {1: _SELL10, 4: _BUY10})
    entry, exit_ = result.fills
    assert (entry.bar_index, exit_.bar_index) == (2, 5) and result.position_quantity == 0
    assert result.borrow_cost == compute_borrow_cost(
        cfg.costs, notional=_D("10") * entry.price, entry_time=cols.ts[2], exit_time=cols.ts[5]
    )
    assert result.borrow_cost > 0 and result.funding_cost == 0


def test_same_input_same_fill_log() -> None:
    plan = {1: _BUY10, 3: _SELL10, 5: _BUY10}
    a, b = _run(_config(), _columns(_CLOSES), plan), _run(_config(), _columns(_CLOSES), plan)
    assert a.fills == b.fills and repr(a.fills) == repr(b.fills) and a == b


def test_window_blocks_future_bars_but_allows_negative_index() -> None:
    cols = _columns(_CLOSES)
    window = BarWindow(cols, 3)
    assert len(window) == 3 and window.close(-1) == cols.close[2] == window.close(2)
    with pytest.raises(LookAheadError):
        window.close(3)
    with pytest.raises(LookAheadError):
        window.open(-4)

    class _Peek:
        def on_bar(self, w: BarWindow, position: PositionState) -> OrderIntent | None:
            return None if w.close(len(w)) else None

    with pytest.raises(LookAheadError):
        run_quick_backtest(
            _config(), cols, timeframe=Timeframe.M1, strategy=_Peek(), initial_cash=_CASH
        )


def test_order_without_execution_bar_expires_not_fills() -> None:
    result = _run(_config(), _columns(_CLOSES), {7: _BUY10})  # 마지막 봉 신호 → 체결 봉 없음
    assert result.fills == () and result.expired_orders == 1


def test_negative_inputs_fail_closed() -> None:
    cols = _columns(_CLOSES)
    with pytest.raises(TooManyBarsError):
        _run(_config(), cols, {}, max_bars=3)
    with pytest.raises(QuickBacktestInputError):
        _run(_config(costs=CostsConfig(funding=True, borrow_apr=None)), cols, {})
    with pytest.raises(QuickBacktestInputError):
        _run(_config(), _columns([]), {})
    with pytest.raises(OrderTypeDisabledError):
        _run(_config(order_types=OrderTypesConfig(limit=False, stop=True, oco=True, trailing=True)),
             cols, {1: OrderIntent(OrderSide.BUY, _D("1"), "limit", _D("90"))})
    with pytest.raises(QuickBacktestInputError):
        _run(_config(), cols, {1: OrderIntent(OrderSide.BUY, _D("1"), "limit", None)})
    with pytest.raises(QuickBacktestInputError):
        _run(_config(), cols, {1: OrderIntent(OrderSide.BUY, _D("0"))})
    with pytest.raises(IncompatibleMagnifierTimeframeError):
        _run(_config(magnifier_tf=Timeframe.H1), cols, {})


def test_magnifier_without_lower_columns_warns_and_uses_lower_when_given() -> None:
    m5 = CandleColumns(
        ts=[_T0, _T0 + timedelta(minutes=5)], open=[_D("100"), _D("100")],
        high=[_D("110"), _D("110")], low=[_D("90"), _D("90")], close=[_D("105"), _D("105")],
        volume=[_D("1000")] * 2, quote_volume=[None] * 2,
    )
    cfg = _config(magnifier_tf=Timeframe.M1)
    fallback = run_quick_backtest(
        cfg, m5, timeframe=Timeframe.M5, strategy=_Scripted({}), initial_cash=_CASH
    )
    assert fallback.warnings and "lower_columns" in fallback.warnings[0]

    # 하위 M1 봉: 봉 1(5분 창) 안에서 고가가 먼저(108) 나오고 저가(91)는 뒤에 온다 →
    # 매수 스탑 105는 하위 봉 순서상 108 세그먼트에서 트리거(봉 단위 확대라면 low→high 순서).
    lower = _columns(["100", "108", "104", "91", "105"], volume="1000")
    lower = CandleColumns(
        ts=[m5.ts[1] + timedelta(minutes=i) for i in range(5)], open=lower.open, high=lower.high,
        low=lower.low, close=lower.close, volume=lower.volume, quote_volume=lower.quote_volume,
    )
    stop = OrderIntent(OrderSide.BUY, _D("1"), order_type="stop", trigger_price=_D("105"))
    magnified = run_quick_backtest(
        cfg, m5, timeframe=Timeframe.M5, strategy=_Scripted({0: stop}), initial_cash=_CASH,
        lower_columns=lower,
    )
    assert magnified.warnings == () and [f.bar_index for f in magnified.fills] == [1]
    assert magnified.fills[0].price == apply_slippage(
        cfg.slippage, side=OrderSide.BUY, reference_price=_D("105"), quantity=_D("1"),
        bar_volume=m5.volume[1],
    )
