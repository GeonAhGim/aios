"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 — `runtime/interpreter.py`
결과·빌트인 디스패치 타입. `interpreter.py`를 300줄 캡(P6.line_cap, `src/core/script`)
아래로 유지하기 위해 분리했다 — `_Machine` 실행 로직과는 다른 변경 축(값 모양)이다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from src.core.script.grammar.ast import Expr
from src.core.script.runtime.series import ScriptRuntimeError, Value
from src.core.script.typing.types import Type

# ADR-2026-09-26-B SBX-1: dynamic runtime op-count budget for `_Machine.run` -- separate
# fail-closed path from DSL-6's static compile-time cap (`ScriptResourceLimitError`,
# analysis/resources.py), which cannot see an instruction count only known at run time.
SCRIPT_RUNTIME_LIMIT: Final[int] = 1_000_000


class ScriptRuntimeLimitError(ScriptRuntimeError):
    """`_Machine.run`'s executed-instruction count exceeded `SCRIPT_RUNTIME_LIMIT`."""


@dataclass(frozen=True)
class CallSite:
    """빌트인에 넘기는 호출 문맥. 반환값은 `result_type`·`bar_count`와 대조된다."""

    ns: str
    ident: str
    result_type: Type
    bar_count: int


class Builtin(Protocol):
    def __call__(self, args: tuple[Value, ...], site: CallSite) -> Value: ...


BuiltinRegistry = Mapping[tuple[str, str], Builtin]
"""(ns, ident) → 빌트인. DSL-9가 채운다. 인터프리터는 조회만 한다."""


@dataclass(frozen=True)
class PlotOutput:
    value: Value
    type: Type
    style: Expr | None


@dataclass(frozen=True)
class OrderOutput:
    when: Value
    side: Expr
    qty_expr: Expr
    opts: Expr | None


@dataclass(frozen=True)
class ExecutionResult:
    bar_count: int
    bindings: Mapping[str, Value]
    """input/let/signal 이름 전부(선언 순서)."""
    signals: Mapping[str, Value]
    plots: tuple[PlotOutput, ...]
    orders: tuple[OrderOutput, ...]
