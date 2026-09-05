"""AIOS Script 런타임 패키지(§9.4 DSL-8) — 값 모델(`series.py`)과 IR 인터프리터(`interpreter.py`).

신규 패키지, `src/core/**` SCAFFOLD zone. 순수(I/O 없음·재귀 없음). 빌트인
본체는 DSL-9가 이 패키지에 추가하고 `BuiltinRegistry`로 주입한다 — 인터프리터는
디스패치 인터페이스만 가진다. DSL-9a: `builtins_math.py`(`math.*`)·`builtins_ta.py`
(`ta.*`, 레지스트리 버전 고정·IND-1 위임), `default_builtins()`로 한 번에 등록.
`builtins_strategy.py`(`strategy.*`)는 DSL-9b.
"""
from __future__ import annotations

from src.core.script.runtime.builtins_math import MATH_BUILTINS, BuiltinCallError
from src.core.script.runtime.builtins_ta import TaBuiltins, TaCall, default_builtins
from src.core.script.runtime.interpreter import (
    Builtin,
    BuiltinRegistry,
    CallSite,
    ExecutionResult,
    OrderOutput,
    PlotOutput,
    execute,
)
from src.core.script.runtime.series import (
    ArithOp,
    CompareOp,
    CrossOp,
    LogicalOp,
    Scalar,
    ScriptRuntimeError,
    Series,
    Value,
    arith,
    broadcast,
    compare,
    cross,
    index,
    logical,
    logical_not,
    negate,
)
from src.core.script.runtime.values import check_value

__all__ = [
    "MATH_BUILTINS",
    "ArithOp",
    "Builtin",
    "BuiltinCallError",
    "BuiltinRegistry",
    "CallSite",
    "CompareOp",
    "CrossOp",
    "ExecutionResult",
    "LogicalOp",
    "OrderOutput",
    "PlotOutput",
    "Scalar",
    "ScriptRuntimeError",
    "Series",
    "TaBuiltins",
    "TaCall",
    "Value",
    "arith",
    "broadcast",
    "check_value",
    "compare",
    "cross",
    "default_builtins",
    "execute",
    "index",
    "logical",
    "logical_not",
    "negate",
]
