"""AIOS Script runtime package (§9.4 DSL-8) — value models (`series.py`) and IR interpreter (`interpreter.py`).

New package under `src/core/**` SCAFFOLD zone. Pure (no I/O, no recursion). Builtin
implementations are added to this package by DSL-9 and injected via `BuiltinRegistry` — the
interpreter holds only a dispatch interface. DSL-9a: `builtins_math.py` (`math.*`) and
`builtins_ta.py` (`ta.*`, fixed registry version, IND-1 delegation), registered at once via
`default_builtins()`. `builtins_strategy.py` (`strategy.*`) is DSL-9b.
"""

from __future__ import annotations

from src.core.script.runtime.builtins_math import MATH_BUILTINS, BuiltinCallError
from src.core.script.runtime.builtins_strategy import (
    STRATEGY_KINDS,
    StrategyBuiltins,
    StrategyIntent,
    intents_to_bytes,
)
from src.core.script.runtime.builtins_ta import TaBuiltins, TaCall, default_builtins
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.interpreter_types import (
    Builtin,
    BuiltinRegistry,
    CallSite,
    ExecutionResult,
    OrderOutput,
    PlotOutput,
)
from src.core.script.runtime.mtf import (
    confirmed_source_index,
    resample_confirmed,
    resolve_ratio,
    timeframe_minutes,
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
    "STRATEGY_KINDS",
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
    "StrategyBuiltins",
    "StrategyIntent",
    "TaBuiltins",
    "TaCall",
    "Value",
    "arith",
    "broadcast",
    "check_value",
    "compare",
    "confirmed_source_index",
    "cross",
    "default_builtins",
    "execute",
    "index",
    "intents_to_bytes",
    "logical",
    "logical_not",
    "negate",
    "resample_confirmed",
    "resolve_ratio",
    "timeframe_minutes",
]
