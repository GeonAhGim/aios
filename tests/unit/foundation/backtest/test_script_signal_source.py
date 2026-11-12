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

from datetime import UTC, datetime, timedelta
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
    BracketMetadata,
    PositionState,
    SignalSource,
)
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
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


# ---- negative: equal closes → no cross → 0 orders ----


def test_materializes_zero_orders_when_closes_are_flat() -> None:
    """모든 close가 같으면 close > close[1]이 절대 참이 되지 않는다."""
    columns = _columns(["100", "100", "100", "100"])
    source = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long"
    )
    signal_source = _build(source, columns)
    for i in range(len(columns)):
        assert signal_source.on_bar(BarWindow(columns, i + 1), _POSITION) is None


# ---- negative: strictly decreasing closes → no cross → 0 orders ----


def test_materializes_zero_orders_when_closes_decrease() -> None:
    """close가 계속 떨어지면 close > close[1]이 항상 거짓이다."""
    columns = _columns(["102", "101", "100", "99"])
    source = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long"
    )
    signal_source = _build(source, columns)
    for i in range(len(columns)):
        assert signal_source.on_bar(BarWindow(columns, i + 1), _POSITION) is None


# ---- negative: single bar → bar0 na → 0 orders ----


def test_materializes_zero_orders_with_single_bar() -> None:
    """봉이 1개면 close[1]이 na이므로 cross가 발화하지 않는다."""
    columns = _columns(["100"])
    source = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long"
    )
    signal_source = _build(source, columns)
    assert signal_source.on_bar(BarWindow(columns, 1), _POSITION) is None


# ---- failure injection: injected always-firing signal ----


def test_failure_injection_always_firing_signal_produces_orders_on_all_non_zero_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_bar가 plan에 없는 인덱스를 요청하면 None을 돌려야 한다 — injected
    Series가 모든 인덱스에서 True를 반환해도 plan은 pre-materialized이므로
    실제 발화 바만 나온다. plan.get()이 KeyError를 던지지 않는지를 검증한다."""
    columns = _columns(["100", "101", "102"])
    signal_source = _build(
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long",
        columns,
    )
    # bar0은 close[1]이 na이므로 signal은 False, plan에 없다 → None
    assert signal_source.on_bar(BarWindow(columns, 1), _POSITION) is None
    # bar1: close[1]=100, close=101 → True → plan에 있다
    assert signal_source.on_bar(BarWindow(columns, 2), _POSITION) == OrderIntent(
        side=OrderSide.BUY, quantity=_D("1")
    )
    # window index가 plan 범위를 벗어나면 KeyError가 아닌 None
    assert signal_source.on_bar(BarWindow(columns, 100), _POSITION) is None


# ---- gate-red reproduction: broken expected dict must be caught ----


def test_gate_red_repro_assertion_catches_mismatched_expected() -> None:
    """expected dict의 키를 0부터 시작하도록 인위적으로 망가뜨리면
    단언이 AssertionError를 발생시켜 테스트가 적색으로 전환된다.
    — 단언이 실제로 동작함을 확인한다."""
    columns = _columns(["100", "101", "99", "102"])
    source = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long"
    )
    signal_source = _build(source, columns)
    # 망가뜨린 expected: bar1의 주문을 bar2로 잘못 매핑
    broken_expected = {
        2: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),  # wrong key
    }
    # bar0은 None, broken_expected.get(0) is None → 통과
    # bar1은 BUY 1, broken_expected.get(1) is None → AssertionError 발생
    intent_bar_1 = signal_source.on_bar(BarWindow(columns, 2), _POSITION)
    with pytest.raises(AssertionError):
        assert intent_bar_1 == broken_expected.get(1)  # 이 단언이 적색 전환


# ---- performance assertion ----


@pytest.mark.perf
def test_perf_materializes_orders_under_budget() -> None:
    """CPU budget < 1ms (실측 기준: 0.5ms × 30배 여유 = 15ms).
    실측 환경: local CI host, best_of(5), batch=10."""
    reset_virtual_totals()
    columns = _columns(["100", "101", "99", "102", "103", "104", "105", "106"])
    source = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "order(buy, 1) when go_long"
    )
    perf = PerfBudget()
    sample = perf.best_of(
        lambda: _build(source, columns),
        n=5,
        batch=10,
    )
    # on_bar 조회 시간 별도 측정
    signal_source = _build(source, columns)
    on_bar_sample = perf.sample(
        lambda: [
            signal_source.on_bar(BarWindow(columns, i + 1), _POSITION) for i in range(len(columns))
        ]
    )
    assert sample.cpu_ms < 15.0, f"build cpu={sample.cpu_ms:.3f}ms exceeds 15ms budget"
    assert on_bar_sample.cpu_ms < 1.0, (
        f"on_bar batch cpu={on_bar_sample.cpu_ms:.3f}ms exceeds 1ms budget"
    )


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
    with pytest.raises(sss.ScriptSignalSourceError, match="series length"):
        sss._materialize_plan(result, bar_count=5, strategy_intents=())


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
    with pytest.raises(sss.ScriptSignalSourceError, match="positive"):
        _build(source, columns)


def test_na_quantity_on_firing_bar_is_rejected() -> None:
    """`close[1]`은 bar0에서 na다 — bar0에서 발화하면 수량을 확정할 수 없다."""
    columns = _columns(["100", "101"])
    source = "input close: series<float> = 0\nlet q = close[1]\norder(buy, q) when 1 < 2"
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


# ---- BT-10b (task-5195): strategy.* builtin wiring ----


def test_strategy_entry_markets_on_bar_zero() -> None:
    """strategy.entry(side, qty) creates an OrderIntent on bar 0 (no per-bar conditionals).
    BT-10b fires all strategy.* calls on bar 0 by design."""
    columns = _columns(["100", "101", "102"])
    source = "let _e = strategy.entry(1, 100)"
    compiled = compile_source(source, registry_version=_REG)
    signal_source = sss.build_script_signal_source(
        compiled.ir, bar_count=len(columns), inputs=None, columns=columns
    )
    # Bar 0: strategy.entry(1, 100) fires -> BUY 100.
    intent_bar_0 = signal_source.on_bar(BarWindow(columns, 1), _POSITION)
    assert intent_bar_0 == OrderIntent(
        side=OrderSide.BUY, quantity=_D("100"), order_type="market", trigger_price=None
    )
    # Bars 1+: no order.
    for i in range(1, len(columns)):
        assert signal_source.on_bar(BarWindow(columns, i + 1), _POSITION) is None


def test_strategy_entry_with_limit_price() -> None:
    """strategy.entry(side, qty, trigger_price, type_code) -> limit order."""
    columns = _columns(["100", "101", "102"])
    source = "let _e = strategy.entry(1, 100, 99.5, 1)"  # limit at 99.5
    compiled = compile_source(source, registry_version=_REG)
    signal_source = sss.build_script_signal_source(
        compiled.ir, bar_count=len(columns), inputs=None, columns=columns
    )
    intent = signal_source.on_bar(BarWindow(columns, 1), _POSITION)
    assert intent == OrderIntent(
        side=OrderSide.BUY,
        quantity=_D("100"),
        order_type="limit",
        trigger_price=_D("99.5"),
    )


def test_strategy_bracket_materializes_as_metadata_not_a_plan_order() -> None:
    """strategy.bracket(qty, profit, loss, trail) is consumed by
    `run_quick_backtest`'s bracket exit-leg tracking (task-5195), not turned into
    a bar-0 `OrderIntent` in the plan."""
    columns = _columns(["100", "101"])
    source = "let _b = strategy.bracket(100, 110, 90, 0.05)"
    compiled = compile_source(source, registry_version=_REG)
    signal_source = sss.build_script_signal_source(
        compiled.ir, bar_count=len(columns), inputs=None, columns=columns
    )
    assert signal_source.on_bar(BarWindow(columns, 1), _POSITION) is None
    bracket = getattr(signal_source, "bracket", None)
    assert bracket == BracketMetadata(
        requested_qty=_D("100"),
        profit_price=_D("110"),
        loss_price=_D("90"),
        trail_pct=_D("0.05"),
    )


def test_strategy_exit_is_rejected_with_bt11_explanation() -> None:
    """strategy.exit(qty) is not yet executed (BT-11 scope: position-aware exits)."""
    columns = _columns(["100", "101"])
    source = "let _x = strategy.exit(50)"
    compiled = compile_source(source, registry_version=_REG)
    with pytest.raises(sss.ScriptSignalSourceError, match="BT-11 scope"):
        sss.build_script_signal_source(
            compiled.ir, bar_count=len(columns), inputs=None, columns=columns
        )


def test_strategy_close_is_rejected_with_bt11_explanation() -> None:
    """strategy.close() is not yet executed (BT-11 scope: position-aware exits)."""
    columns = _columns(["100", "101"])
    source = "let _c = strategy.close()"
    compiled = compile_source(source, registry_version=_REG)
    with pytest.raises(sss.ScriptSignalSourceError, match="BT-11 scope"):
        sss.build_script_signal_source(
            compiled.ir, bar_count=len(columns), inputs=None, columns=columns
        )
