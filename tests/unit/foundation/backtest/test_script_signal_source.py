"""BT-10b `script_signal_source` — 브리지 물질화·미래참조 차단·negative(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-10(호출자 접점), task-1625 decision. DoD 검증: (1) 전 구간 1회 실행 후
봉 인덱스별 사전 물질화(`on_bar`는 조회만), (3) 미래참조 fail-closed, (5)
negative 4종 이상(orders 0개/when 시리즈 길이 불일치/side·qty 비상수/qty<=0·
na), (6) `ScriptRuntimeError`(및 그 하위) 전파. 컴파일·실행은 DSL-12
`compile_source`/DSL-8 `execute`를 그대로 쓴다 — 이 테스트는 렉서·파서·
인터프리터를 재구현하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.script.artifact.compile import compile_source
from src.core.script.grammar.ast import Identifier, NumberLiteral
from src.core.script.runtime.interpreter import ExecutionResult, OrderOutput
from src.core.script.runtime.series import Series, Value
from src.data.models.trading import OrderSide
from src.foundation.backtest.application import script_signal_source as sss
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    PositionState,
    SignalSource,
)
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_REG = "r" * 64
_D = Decimal
_POSITION = PositionState(quantity=_D("0"), cash=_D("100000"), has_pending_order=False)


def _columns(closes: list[str]) -> CandleColumns:
    c = [_D(x) for x in closes]
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(len(c))],
        open=list(c),
        high=[v + 1 for v in c],
        low=[v - 1 for v in c],
        close=c,
        volume=[_D("1000")] * len(c),
        quote_volume=[None] * len(c),
    )


def _build(
    source: str, columns: CandleColumns, *, inputs: dict[str, Value] | None = None
) -> SignalSource:
    compiled = compile_source(source, registry_version=_REG)
    return sss.build_script_signal_source(
        compiled.ir, bar_count=len(columns), inputs=inputs, columns=columns
    )


# ---- 골든 경로: 사전 물질화가 인터프리터 결과와 일치 ----


def test_materializes_orders_from_close_crossing_signal() -> None:
    """close > close[1]일 때만 매수 1주. bar0은 close[1]이 na라 발화하지 않는다."""
    columns = _columns(["100", "101", "99", "102"])
    source = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long"
    )
    signal_source = _build(source, columns)
    expected = {
        1: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
        3: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
    }
    for i in range(len(columns)):
        intent = signal_source.on_bar(BarWindow(columns, i + 1), _POSITION)
        assert intent == expected.get(i)


def test_on_bar_never_leaks_a_future_bars_order() -> None:
    """미래참조 fail-closed(단언): 주문이 마지막 봉에만 있으면, 그보다 짧은
    창으로는 절대 그 주문이 보이지 않는다 — on_bar는 `len(window)-1`만 조회한다."""
    columns = _columns(["100", "101", "102", "103", "104"])
    last = len(columns) - 1
    # bool 스칼라 입력을 봉마다 다른 값으로 공급해(inputs) 마지막 봉에서만 발화시킨다.
    source = "input fire: bool = 0\norder(sell, 2) when fire"
    fires = [False] * len(columns)
    fires[last] = True
    compiled = compile_source(source, registry_version=_REG)
    signal_source = sss.build_script_signal_source(
        compiled.ir,
        bar_count=len(columns),
        inputs={"fire": Series.of_bools(fires)},
        columns=columns,
    )
    for i in range(len(columns)):
        intent = signal_source.on_bar(BarWindow(columns, i + 1), _POSITION)
        if i == last:
            assert intent == OrderIntent(side=OrderSide.SELL, quantity=_D("2"))
        else:
            assert intent is None


# ---- negative: orders 0개 ----


def test_no_orders_script_always_returns_none() -> None:
    columns = _columns(["100", "101", "102"])
    source = "input length: int = 3\nsignal always = 1 < 2"
    signal_source = _build(source, columns)
    for i in range(len(columns)):
        assert signal_source.on_bar(BarWindow(columns, i + 1), _POSITION) is None


# ---- negative: when 시리즈 길이 != bar_count ----


def test_when_series_length_mismatch_is_rejected() -> None:
    bad_when = Series((True, True, True))
    order = OrderOutput(
        when=bad_when, side=Identifier(name="buy"), qty_expr=NumberLiteral(value=1), opts=None
    )
    result = ExecutionResult(bar_count=5, bindings={}, signals={}, plots=(), orders=(order,))
    with pytest.raises(sss.ScriptSignalSourceError, match="시리즈 길이"):
        sss._materialize_plan(result, bar_count=5)


# ---- negative: side/qty가 상수로 접히지 않는다 ----


def test_side_expression_that_is_not_a_bare_identifier_is_rejected() -> None:
    columns = _columns(["100", "101"])
    source = "order(not buy, 1) when 1 < 2"
    with pytest.raises(sss.ScriptSignalSourceError, match="side"):
        _build(source, columns)


def test_qty_expr_that_does_not_fold_to_constant_or_binding_is_rejected() -> None:
    columns = _columns(["100", "101"])
    source = "input length: int = 3\norder(buy, length + 1) when 1 < 2"
    with pytest.raises(sss.ScriptSignalSourceError, match="qty_expr"):
        _build(source, columns)


def test_unknown_side_identifier_is_rejected() -> None:
    columns = _columns(["100", "101"])
    source = "order(hold, 1) when 1 < 2"
    with pytest.raises(sss.ScriptSignalSourceError, match="side"):
        _build(source, columns)


# ---- negative: qty <= 0 / na 거부 ----


def test_zero_quantity_on_firing_bar_is_rejected() -> None:
    columns = _columns(["100", "101"])
    source = "input qty: int = 0\norder(buy, qty) when 1 < 2"
    with pytest.raises(sss.ScriptSignalSourceError, match="양수"):
        _build(source, columns)


def test_na_quantity_on_firing_bar_is_rejected() -> None:
    """`close[1]`은 bar0에서 na다 — bar0에서 발화하면 수량을 확정할 수 없다."""
    columns = _columns(["100", "101"])
    source = (
        "input close: series<float> = 0\n"
        "let q = close[1]\n"
        "order(buy, q) when 1 < 2"
    )
    with pytest.raises(sss.ScriptSignalSourceError, match="na"):
        _build(source, columns)


# ---- negative: opts는 아직 의미가 없다(v1 범위 밖, 명시 거부) ----


def test_opts_present_is_rejected_not_guessed() -> None:
    columns = _columns(["100", "101"])
    source = "order(buy, 1, 2) when 1 < 2"
    with pytest.raises(sss.ScriptSignalSourceError, match="opts"):
        _build(source, columns)


# ---- negative: 봉 길이 불일치 입력 자체를 거부 ----


def test_columns_length_mismatch_with_bar_count_is_rejected() -> None:
    columns = _columns(["100", "101", "102"])
    compiled = compile_source("signal always = 1 < 2", registry_version=_REG)
    with pytest.raises(sss.ScriptSignalSourceError, match="bar_count"):
        sss.build_script_signal_source(compiled.ir, bar_count=5, inputs=None, columns=columns)
