"""L4_analytics_authoring_backtest_marketplace_v1.0.md §1.2/§9.4 DSL-11 —
AIOS Script facade in front of the FROZEN_PAPER_ONLY strategy engine
(`src/core/strategy/**`).

This is the single touchpoint between the frozen cond-v2 engine and the AIOS
Script pipeline (§1.2: execution engine `src/core/strategy/**` is
FROZEN_PAPER_ONLY, the only contact point is this DSL-11 facade). It does not
modify, subclass, or re-implement `engine.py` / `condition_evaluator.py` /
`models.py` — it only reads `ConditionEvaluator` (read-only import) to drive
the existing cond-v2 semantics, and delegates the AIOS Script side entirely to
DSL-9..12 (bridge, compile, execute). No lexer/parser/type-checker/IR/runtime
logic lives here (§C: no duplicate context).

Both paths below consume the *same* `IncrementalIndicator` (IND-1) instances —
one per (indicator, params) pair, derived from DSL-10's `bridge_cond_v2` atom
metadata rather than re-parsing cond-v2 keys a second time — so indicator
values are identical between the frozen path and the script path by
construction, not by floating-point luck. `condition_signal_series` walks bar
by bar with the FROZEN `ConditionEvaluator`; `script_signal_series` runs the
bridged AIOS Script vectorized over the whole candle window via DSL-8's
interpreter.

Fail-closed: `script_signal_series` and `compile_script_source` propagate
`CondV2BridgeError` / `ScriptCompileError` unchanged. There is no
except-and-fall-back-to-cond-v2 anywhere in this module.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from src.core.indicators.engine.incremental import IncrementalIndicator
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.script.artifact import CompiledScript, ScriptCompileError, compile_source
from src.core.script.compat import SIGNAL_NAME, BridgedScript, CondV2BridgeError, bridge_cond_v2
from src.core.script.runtime import Scalar, Series, default_builtins, execute
from src.core.strategy.condition_evaluator import ConditionEvaluator, IndicatorDataMissingError

Candle = Mapping[str, float]

__all__ = [
    "CondV2BridgeError",
    "ScriptCompileError",
    "compile_script_source",
    "condition_signal_series",
    "market_state_series",
    "script_signal_series",
]


def market_state_series(
    expression: str,
    candles: Sequence[Candle],
    *,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> list[dict[str, float]]:
    """Per-bar `market_state` dicts for `expression`'s indicators.

    Built with the same streaming engine (IND-1 `IncrementalIndicator`) DSL-9's
    `ta.*` builtins use internally, one engine per distinct cond-v2 key — the
    (indicator, params) pairs come from DSL-10's `bridge_cond_v2` atom
    metadata, so this never re-parses the cond-v2 key grammar a second time. A
    key is absent from a bar's dict until its indicator warms up (lookback),
    matching `ConditionEvaluator`'s "missing key = judgement withheld".
    """
    bridged = bridge_cond_v2(expression, registry=registry)
    engines: dict[str, tuple[IncrementalIndicator, str]] = {}
    for node in bridged.compat_map["nodes"].values():
        if node["kind"] != "atom" or node["key"] in engines:
            continue
        indicator = node["call"]["ident"].upper()
        spec = registry.get(indicator)
        params = dict(zip((p.name for p in spec.params), node["call"]["args"], strict=True))
        engines[node["key"]] = (IncrementalIndicator(indicator, params, registry), spec.outputs[0])
    states: list[dict[str, float]] = []
    for candle in candles:
        state: dict[str, float] = {}
        for key, (engine, output) in engines.items():
            value = engine.update(candle)[output]
            if value is not None:
                state[key] = value
        states.append(state)
    return states


def condition_signal_series(
    expression: str,
    candles: Sequence[Candle],
    *,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> tuple[bool | None, ...]:
    """FROZEN cond-v2 path, walked bar by bar with `ConditionEvaluator`
    (read-only import — never modified by this module). `None` = judgement
    withheld (`IndicatorDataMissingError`), the same "no signal" outcome
    `StrategyEngine.evaluate` gives that case (its `continue` on the
    exception)."""
    evaluator = ConditionEvaluator()
    out: list[bool | None] = []
    prev: dict[str, float] | None = None
    for state in market_state_series(expression, candles, registry=registry):
        try:
            out.append(evaluator.evaluate(expression, state, prev))
        except IndicatorDataMissingError:
            out.append(None)
        prev = state
    return tuple(out)


def compile_script_source(source: str, *, registry_version: str) -> CompiledScript:
    """Thin delegate to DSL-12's `compile_source` — no lexer/parser/type-checker/
    IR-lowering logic lives in this facade (§C: no duplicate context). Compile
    failures (`ScriptCompileError`, one of the §3.3 4 taxonomy codes) propagate
    unchanged: fail-closed, no silent fallback to `condition_signal_series`."""
    return compile_source(source, registry_version=registry_version)


def script_signal_series(
    expression: str,
    candles: Sequence[Candle],
    *,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> tuple[Scalar, ...]:
    """AIOS Script path: DSL-10 bridge -> DSL-12 compile -> DSL-8 execute, using
    DSL-9's real `ta.*` builtins. `CondV2BridgeError` (bridge rejects the
    expression) / `ScriptCompileError` (compile rejects the bridged source)
    propagate unchanged (fail-closed) — never caught here, never silently
    routed to `condition_signal_series` instead."""
    bridged: BridgedScript = bridge_cond_v2(expression, registry=registry)
    registry_version = registry.registry_hash()
    compiled = compile_script_source(bridged.source, registry_version=registry_version)
    bar_count = len(candles)
    inputs = {
        name: Series.of_floats(candle[name] for candle in candles)
        for name in bridged.compat_map["inputs"]
    }
    builtins_table = default_builtins(registry, registry_version=registry_version)
    result = execute(compiled.ir, bar_count=bar_count, inputs=inputs, builtins=builtins_table)
    # DSL-4: `signal cond` is always typed `series<bool>`.
    signal = cast(Series, result.signals[SIGNAL_NAME])
    return signal.values
