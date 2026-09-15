"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-11 —
`src/core/strategy/script_facade.py` regression tests.

DoD (task-2182 decision): (a) existing `src/core/strategy/**` unit tests stay
green untouched (verified by CI running them alongside this file, not by this
file); (b) same candle input -> cond-v2 path and AIOS Script path fire the
exact same boolean signal sequence (`True`/`False` — na collapses to
"no fire" on both sides, exactly like a live signal consumer would treat it);
(c) a script that fails to compile is rejected with an explicit exception,
never silently routed to the cond-v2 path; (d) the facade only delegates
(DSL-2..9 lexer/parser/IR/runtime are not re-implemented here).

task-2931(DEEPEN, docs/audit/DEPTH_DSL_IND.md task-2182 행): this table's
shallowest `negative` coverage (2, <3) for a FROZEN-adjacent facade. Adds a
3rd+ plain negative (unknown indicator), a failure-injection test (simulated
DSL-8 crash must propagate, never silently fall back to cond-v2), a numeric
performance assertion (script-path overhead budget over cond-v2), and a
gate-red reproduction proving the cond-v2/script parity check is a real
regression detector for the FROZEN `ConditionEvaluator`, not vacuously green.
"""

from __future__ import annotations

import math
import time

import pytest

from src.core.indicators.registry import DEFAULT_REGISTRY
from src.core.script.artifact import ScriptCompileError
from src.core.script.compat import CondV2BridgeError, bridge_cond_v2
from src.core.script.runtime import Scalar
from src.core.strategy import script_facade
from src.core.strategy.condition_evaluator import ConditionEvaluator
from src.core.strategy.script_facade import (
    compile_script_source,
    condition_signal_series,
    script_signal_series,
)


def _candles(n: int) -> list[dict[str, float]]:
    """Deterministic synthetic OHLCV series — oscillating trend so RSI/EMA/SMA
    cross thresholds both ways, giving both `True` and `False` bars."""
    candles: list[dict[str, float]] = []
    prev_close = 100.0
    for i in range(n):
        close = 100.0 + 12.0 * math.sin(i / 4.5) + 0.15 * i
        high = max(prev_close, close) + 1.5
        low = min(prev_close, close) - 1.5
        volume = 1000.0 + 25.0 * (i % 9)
        candles.append(
            {"open": prev_close, "high": high, "low": low, "close": close, "volume": volume}
        )
        prev_close = close
    return candles


CANDLES = _candles(60)

# Every indicator DSL-10's bridge supports (SMA/EMA/RSI/ATR/CCI/WILLR/MFI/OBV,
# the full `_TA_IDENT` table), across single-atom, AND, OR, and CROSSES forms.
EXPRESSIONS = [
    "RSI > 50",
    "RSI < 50 AND SMA_timeperiod10 > 90",
    "RSI CROSSES_ABOVE 50",
    "RSI CROSSES_BELOW 50",
    "OBV > 0 OR ATR_timeperiod7 CROSSES_BELOW 5",
    "EMA_timeperiod9 > 95 AND CCI_timeperiod10 < 50",
    "WILLR_timeperiod14 < -20",
    "MFI_timeperiod14 > 40",
]


def _fires(series: tuple[Scalar, ...] | tuple[bool | None, ...]) -> tuple[bool, ...]:
    """Collapse tri-state (True/False/na) to the boolean a signal consumer acts
    on: only `True` is a fire. Both `False` and na (`None`) mean "no trade" —
    exactly the distinction `StrategyEngine.evaluate` makes (only a matched
    transition produces a `Signal`)."""
    return tuple(v is True for v in series)


# ---- (b) same-input, same-signal parity ----


@pytest.mark.parametrize("expression", EXPRESSIONS)
def test_script_path_fires_on_exactly_the_same_bars_as_cond_v2(expression: str) -> None:
    cond_v2 = condition_signal_series(expression, CANDLES)
    script = script_signal_series(expression, CANDLES)
    assert len(cond_v2) == len(script) == len(CANDLES)
    assert _fires(cond_v2) == _fires(script)
    # sanity: the fixture isn't degenerate (both a fire and a non-fire exist)
    assert True in _fires(cond_v2) and False in _fires(cond_v2)


def test_both_paths_agree_on_the_first_bar_that_fires() -> None:
    """The bulk parity above isn't a coincidence of loose thresholds — both
    paths are built from the same `IncrementalIndicator` instances, so they
    agree on the exact bar index of the first `True` fire, not just on
    aggregate counts."""
    expression = "RSI > 50 AND SMA_timeperiod10 > 90"
    cond_v2 = condition_signal_series(expression, CANDLES)
    script = script_signal_series(expression, CANDLES)
    first_true = cond_v2.index(True)
    assert script[first_true] is True
    assert all(v is not True for v in cond_v2[:first_true])
    assert all(v is not True for v in script[:first_true])


# ---- (c) fail-closed: no silent fallback ----


def test_invalid_script_source_raises_instead_of_silently_compiling() -> None:
    with pytest.raises(ScriptCompileError) as info:
        compile_script_source("let y = 2 @ 3", registry_version="r" * 64)
    assert info.value.code == "SCRIPT_SYNTAX"


def test_bridge_rejected_expression_raises_instead_of_falling_back_to_cond_v2() -> None:
    """`MACD` is a multi-output indicator DSL-10 refuses to bridge
    (`SCRIPT_COMPAT_UNSUPPORTED`). The script path must raise — not silently
    compute nothing, and not silently reuse the cond-v2 result instead."""
    with pytest.raises(CondV2BridgeError) as info:
        script_signal_series("MACD > 0", CANDLES)
    assert info.value.code == "SCRIPT_COMPAT_UNSUPPORTED"


def test_unknown_indicator_raises_instead_of_producing_a_wrong_signal() -> None:
    """3rd plain negative (task-2931 DEEPEN): an indicator the registry has
    never heard of must reject at the bridge boundary, not silently produce a
    series of `False`/na that a caller could mistake for "never fires"."""
    with pytest.raises(CondV2BridgeError) as info:
        script_signal_series("NOTAREALINDICATOR > 50", CANDLES)
    assert info.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_script_execution_crash_propagates_instead_of_silently_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 (task-2931 DEEPEN): simulate DSL-8's `execute` crashing (an
    interpreter bug, not a rejected-input error). `script_signal_series` must
    let it propagate unchanged — the module docstring's "no
    except-and-fall-back-to-cond-v2 anywhere" claim, proven under an actual
    injected fault rather than only under the two expected-error paths above.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated DSL-8 interpreter crash")

    monkeypatch.setattr(script_facade, "execute", _boom)
    with pytest.raises(RuntimeError, match="simulated DSL-8 interpreter crash"):
        script_signal_series("RSI > 50", CANDLES)


# ---- 게이트 적색 재현: FROZEN 파일 무수정 회귀 가드 ----


def test_frozen_evaluator_drift_is_caught_by_the_parity_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현 (task-2931 DEEPEN): this facade's entire DoD (b) rests
    on `ConditionEvaluator` (FROZEN, read-only import — never modified by this
    module) and the AIOS Script bridge staying in lock-step. Monkeypatch the
    FROZEN class's `evaluate` to invert its verdict — simulating what a real
    drift in `condition_evaluator.py` would look like — without touching the
    FROZEN file itself. This proves the parity assertion
    `test_script_path_fires_on_exactly_the_same_bars_as_cond_v2` relies on is
    a real regression detector that goes red on drift, not vacuously green
    because nothing in this suite ever exercises disagreement.
    """
    expression = "RSI > 50"
    script = script_signal_series(expression, CANDLES)

    real_evaluate = ConditionEvaluator.evaluate

    def _inverted(self: ConditionEvaluator, *args: object, **kwargs: object) -> bool | None:
        result = real_evaluate(self, *args, **kwargs)  # type: ignore[arg-type]
        return result if result is None else not result

    monkeypatch.setattr(ConditionEvaluator, "evaluate", _inverted)
    drifted_cond_v2 = condition_signal_series(expression, CANDLES)

    assert _fires(drifted_cond_v2) != _fires(script)


# ---- 수치 성능 단언: 파사드 오버헤드 ----


def test_script_path_overhead_over_cond_v2_stays_within_budget() -> None:
    """수치 성능 단언 (task-2931 DEEPEN): the AIOS Script path adds a
    bridge+compile+execute layer on top of the same `IncrementalIndicator`
    engines the FROZEN cond-v2 path drives directly. A normalized multiplier
    (not an absolute wall-clock budget) keeps this robust to slow CI
    machines while still catching a runaway facade regression (e.g. an
    accidental re-compile-per-bar or O(n^2) bridge walk)."""
    expression = "RSI > 50 AND SMA_timeperiod10 > 90"
    registry_version = DEFAULT_REGISTRY.registry_hash()
    bridged = bridge_cond_v2(expression, registry=DEFAULT_REGISTRY)
    compile_script_source(bridged.source, registry_version=registry_version)  # warm up caches

    iterations = 20
    start = time.perf_counter()
    for _ in range(iterations):
        condition_signal_series(expression, CANDLES)
    cond_v2_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(iterations):
        script_signal_series(expression, CANDLES)
    script_elapsed = time.perf_counter() - start

    assert script_elapsed <= cond_v2_elapsed * 25 + 0.5


# ---- (d) delegation only: no reimplemented lexer/parser/IR/runtime ----


def test_facade_module_defines_no_classes() -> None:
    """The facade is glue (functions only) — a `class` definition here would
    be a strong signal of re-implementing DSL-2..9 instead of delegating."""
    import ast
    import inspect

    from src.core.strategy import script_facade as facade_mod

    tree = ast.parse(inspect.getsource(facade_mod))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
