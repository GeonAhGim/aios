"""BT-10b — 컴파일된 AIOS Script(DSL) IR → BT-10 SignalSource 브리지.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-10(호출자 접점, task-1504 9a1ae87 `quick_backtest.py`), §9.4 DSL-7(IR,
task-1503)·DSL-8(`interpreter.execute`, task-1522 9fa72b5)·DSL-12
(`compile_source`, task-1535 8c0c10e). BT-10b 자체는 이 스펙 개정에 아직
등재되지 않은 신규 리프다(task-1625 decision) — DSL-11 `script_facade`
(`src/core/strategy`, FROZEN_PAPER_ONLY)와는 다른 소비자이며 그것을 선점·
대체하지 않는다.

I-05(백테스트·라이브가 같은 컴파일 산출물·같은 도메인 로직을 공유)를 지키려고
`interpreter.execute()`를 다시 구현하지 않는다 — `bar_count` 구간 전체를
정확히 한 번만 실행하고, 산출물(`ExecutionResult.orders`)의
`OrderOutput.when`/`qty_expr`/`side`를 봉 인덱스별 `OrderIntent`(quick_backtest
계약)로 미리 물질화한다. `on_bar`는 그 표를 인덱스로 조회만 하므로 봉마다
인터프리터를 다시 돌리지 않고(성능), 구조적으로 현재 인덱스 이외의 값을
반환할 수 없다(미래 참조 불가능 — look-ahead가 아니라 애초에 다른 인덱스를
꺼낼 방법이 없다).

`Order.side`/`qty_expr`/`opts`는 DSL-1/4/7/8이 의도적으로 AST 원형만 운반하고
해석하지 않는 자리다(`runtime/interpreter.py` 모듈 docstring: "의미 확정은
DSL-11"). 이 브리지가 백테스트 소비자로서 새로 확정하는 v1 규칙(추측 대신
명시 거부, fail-closed):
- `side`: `buy`/`sell` 식별자만 인정(대소문자 구분, 두 이름 외 예약어 없음 —
  `typing/checker.py` 결정). 그 외 형태·이름은 거부.
- `qty_expr`: 상수 리터럴 또는 이미 실행된 이름(`ExecutionResult.bindings`
  조회 — 재평가가 아니라 `execute()`가 이미 계산해 둔 값을 꺼내는 것) 만
  인정한다. 이항식·호출식 등은 두 번째 인터프리터를 새로 만들어야 하므로
  거부한다(재구현 금지).
- `opts`: 스키마가 아직 어디에도 정의돼 있지 않다(DSL-11 몫, 참고:
  `ir/ops.py`·`typing/checker.py` 모두 "의미 미정의"라고 명시). `None`만
  받고(시장가 주문), 그 외는 거부한다 — `order_type`/`trigger_price`를
  추측으로 지어내지 않는다.
- 한 봉에서 둘 이상의 `order()`가 동시에 발화하면 우선순위가 정의돼 있지
  않으므로 거부한다(fail-closed, 추측 금지).

순수 모듈 — I/O 없음(TID251, backtest/application 존).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from src.core.script.grammar.ast import Expr, Identifier, NumberLiteral
from src.core.script.ir.ops import DeclareInput, IRProgram
from src.core.script.runtime.builtins_ta import default_builtins
from src.core.script.runtime.interpreter import ExecutionResult, execute
from src.core.script.runtime.series import Scalar, ScriptRuntimeError, Series, Value
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    PositionState,
    SignalSource,
)
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["ScriptSignalSourceError", "build_script_signal_source"]

_SIDE_BY_NAME: Mapping[str, OrderSide] = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}


class ScriptSignalSourceError(ScriptRuntimeError):
    """이 브리지가 `order()`의 side/qty_expr/opts를 안전하게 해석할 수 없거나
    물질화된 값이 `OrderIntent` 계약을 어길 때(fail-closed). `ScriptRuntimeError`
    하위라 호출자는 "스크립트 실행·해석 실패"를 한 예외 계열로 잡을 수 있다."""


def build_script_signal_source(
    ir: IRProgram,
    *,
    bar_count: int,
    inputs: Mapping[str, Value] | None = None,
    columns: CandleColumns,
) -> SignalSource:
    """`ir`을 `bar_count`(= `len(columns)`) 구간 전체에 대해 정확히 한 번
    실행하고, 산출된 주문을 봉 인덱스별로 미리 계산해 반환한다. `on_bar`
    호출은 이후 딕셔너리 조회뿐이다(결정론·성능 — 봉마다 인터프리터를 다시
    돌리지 않는다)."""
    if len(columns) != bar_count:
        raise ScriptSignalSourceError(
            f"columns 길이({len(columns)})가 bar_count({bar_count})와 다르다"
        )
    merged_inputs: dict[str, Value] = {**_market_inputs(ir, columns), **dict(inputs or {})}
    result = execute(ir, bar_count=bar_count, inputs=merged_inputs, builtins=default_builtins())
    plan = _materialize_plan(result, bar_count)
    return _MaterializedSignalSource(plan)


@dataclass(frozen=True, slots=True)
class _MaterializedSignalSource:
    plan: Mapping[int, OrderIntent]

    def on_bar(self, window: BarWindow, _position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


def _market_inputs(ir: IRProgram, columns: CandleColumns) -> dict[str, Value]:
    """`open`/`high`/`low`/`close`/`volume`은 예약어가 아니다(`typing/checker.py`
    결정) — 스크립트가 `input <name>: series<float> = 0`으로 실제로 선언한
    경우에만 `CandleColumns`에서 시리즈를 만들어 채운다. 선언하지 않은 이름을
    `inputs`에 얹으면 `execute`가 "선언되지 않은 입력 이름"으로 거부하므로
    무조건 채우지 않는다."""
    declared_series = {
        instr.name
        for instr in ir.instrs
        if isinstance(instr, DeclareInput) and instr.type == "series<float>"
    }
    candidates: dict[str, list[Decimal]] = {
        "open": columns.open,
        "high": columns.high,
        "low": columns.low,
        "close": columns.close,
        "volume": columns.volume,
    }
    return {
        name: Series.of_floats(float(v) for v in values)
        for name, values in candidates.items()
        if name in declared_series
    }


def _materialize_plan(result: ExecutionResult, bar_count: int) -> Mapping[int, OrderIntent]:
    plan: dict[int, OrderIntent] = {}
    for order in result.orders:
        side = _resolve_side(order.side)
        if order.opts is not None:
            raise ScriptSignalSourceError(
                "order()의 opts는 아직 의미가 정의돼 있지 않다(DSL-11 몫) — "
                "이 브리지는 opts가 있는 주문을 거부한다"
            )
        qty_source = _resolve_qty_source(order.qty_expr, result.bindings)
        for i in range(bar_count):
            if _value_at(order.when, i, bar_count) is not True:
                continue
            if i in plan:
                raise ScriptSignalSourceError(
                    f"봉 {i}에서 둘 이상의 order()가 동시에 발화했다 — 우선순위가 정의돼 있지 않다"
                )
            plan[i] = OrderIntent(
                side=side,
                quantity=_to_quantity(_value_at(qty_source, i, bar_count)),
                order_type="market",
                trigger_price=None,
            )
    return MappingProxyType(plan)


def _resolve_side(expr: Expr) -> OrderSide:
    if isinstance(expr, Identifier) and expr.name in _SIDE_BY_NAME:
        return _SIDE_BY_NAME[expr.name]
    raise ScriptSignalSourceError(f"order()의 side는 buy/sell 식별자만 지원한다(받음: {expr!r})")


def _resolve_qty_source(expr: Expr, bindings: Mapping[str, Value]) -> Value:
    if isinstance(expr, NumberLiteral):
        return expr.value
    if isinstance(expr, Identifier):
        if expr.name not in bindings:
            raise ScriptSignalSourceError(
                f"order()의 qty_expr 이름이 바인딩돼 있지 않다: {expr.name!r}"
            )
        return bindings[expr.name]
    raise ScriptSignalSourceError(
        "order()의 qty_expr는 상수 리터럴 또는 이미 바인딩된 이름만 지원한다 — "
        f"이 브리지는 두 번째 인터프리터를 만들지 않는다(받음: {expr!r})"
    )


def _value_at(value: Value, bar: int, bar_count: int) -> Scalar:
    if isinstance(value, Series):
        if len(value) != bar_count:
            raise ScriptSignalSourceError(
                f"시리즈 길이({len(value)})가 봉 수({bar_count})와 다르다"
            )
        return value.at(bar)
    return value


def _to_quantity(raw: Scalar) -> Decimal:
    if raw is None:
        raise ScriptSignalSourceError("주문 수량이 na다 — 발화한 봉에서 수량을 확정할 수 없다")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ScriptSignalSourceError(f"주문 수량이 수치가 아니다: {raw!r}")
    if isinstance(raw, float) and not math.isfinite(raw):
        raise ScriptSignalSourceError(f"주문 수량이 유한수가 아니다: {raw!r}")
    quantity = Decimal(str(raw))
    if quantity.is_nan() or quantity <= 0:
        raise ScriptSignalSourceError(f"주문 수량은 양수여야 한다: {quantity}")
    return quantity
