"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-9a —
`runtime/builtins_ta.py` 테스트.

확인 항목: (1) I-04/I-10 위임 증명 — `ta.*`가 IND-1 `IncrementalIndicator`를 실제로
호출하고(스파이·왕복수) 결과가 엔진(증분·일괄)과 동일, 모듈 안에 지표 산식이
없음(정적 AST), (2) 레지스트리 버전 고정 — `registry_version`이 L02 `registry_hash()`와
같고 DSL-12 `script_hash` 입력과 일치, 불일치는 거부, 호출 장부에 해시 기록,
(3) 호출 규약 — ident 파생(다중 출력), 기본 파라미터, 선행 na 접두, 다중 입력,
(4) negative: 미등록 지표·범위 밖 파라미터·lookback 부족·내부 na·bool·arity는 오류
코드로 거부(폴백 없음). 절대 지연 단언 없음(print + 왕복수 단언).
"""
from __future__ import annotations

import ast
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.core.indicators.engine import incremental
from src.core.indicators.engine.vectorized import compute
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.indicators.specs_talib import TALIB_SPECS
from src.core.script.artifact.hash import script_hash
from src.core.script.grammar.parser import parse
from src.core.script.ir import lower_program
from src.core.script.runtime import (
    BuiltinCallError,
    BuiltinRegistry,
    CallSite,
    ExecutionResult,
    ScriptRuntimeError,
    Series,
    TaBuiltins,
    Value,
    builtins_ta,
    default_builtins,
    execute,
)

_MODULE = Path(builtins_ta.__file__)
N = 40
_RNG = random.Random(1554)
OHLCV = {
    "high": [100 + _RNG.uniform(1, 3) + i * 0.1 for i in range(N)],
    "low": [100 - _RNG.uniform(1, 3) + i * 0.1 for i in range(N)],
    "close": [100 + _RNG.uniform(-1, 1) + i * 0.1 for i in range(N)],
    "volume": [float(_RNG.randint(100, 1000)) for _ in range(N)],
}
INPUTS = {k: Series.of_floats(v) for k, v in OHLCV.items()}
DECLS = "".join(f"input {k}: series<float> = 0\n" for k in OHLCV)


def run(body: str, builtins: BuiltinRegistry | None = None, bars: int = N) -> ExecutionResult:
    inputs = {k: Series.of_floats(v[:bars]) for k, v in OHLCV.items()}
    return execute(lower_program(parse(DECLS + body)), bar_count=bars, inputs=inputs,
                   builtins=default_builtins() if builtins is None else builtins)  # fmt: skip


def engine_series(name: str, params: dict[str, int], output: str = "value") -> Series:
    ind = incremental.IncrementalIndicator(name, params)
    return Series.of_floats(
        [ind.update({k: OHLCV[k][t] for k in ind.spec.inputs})[output] for t in range(N)]
    )


def assert_close(actual: Value, expected: Series) -> None:
    assert isinstance(actual, Series) and len(actual) == len(expected)
    for a, e in zip(actual.values, expected.values, strict=True):
        assert (a is None) == (e is None)
        if a is not None and e is not None:
            assert math.isclose(float(a), float(e), rel_tol=0, abs_tol=1e-9)


# ---- (1) 위임 증명 ----


def test_sma_equals_incremental_engine_and_vectorized_batch() -> None:
    result = run("let m = ta.sma(close, 5)")
    assert_close(result.bindings["m"], engine_series("SMA", {"timeperiod": 5}))
    batch = compute("SMA", {"close": np.array(OHLCV["close"])}, {"timeperiod": 5})["value"]
    expected = Series.of_floats([None if np.isnan(v) else float(v) for v in batch])
    assert_close(result.bindings["m"], expected)


def test_builtin_delegates_every_bar_to_the_incremental_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[str, dict[str, int]]] = []
    updates: list[int] = [0]
    real = incremental.IncrementalIndicator

    class Spy(real):  # type: ignore[misc,valid-type]
        def __init__(self, name: str, params: Any = None, registry: Any = DEFAULT_REGISTRY) -> None:
            super().__init__(name, params, registry)
            created.append((name, dict(self.params)))

        def update(self, bar: Any) -> dict[str, float | None]:
            updates[0] += 1
            return super().update(bar)

    monkeypatch.setattr(builtins_ta, "IncrementalIndicator", Spy)
    ta = TaBuiltins()
    result = run("let r = ta.rsi(close[2], 7)", builtins=ta.table)
    assert created == [("RSI", {"timeperiod": 7})]
    print(f"ta.rsi bar_count={N} fed_from=2 engine.update 왕복수={updates[0]}")
    assert updates[0] == N - 2 and ta.calls[0].fed_from == 2
    r = result.bindings["r"]
    assert isinstance(r, Series) and r.values[:2] == (None, None) and r.values[2 + 7] is not None


def test_module_contains_no_indicator_formula_and_no_numpy() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names = {n.name.lower().strip("_") for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not names & {n.lower() for n in TALIB_SPECS}, names
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "numpy" not in imported and not any("numpy" in (m or "") for m in modules)
    assert "src.core.indicators.engine.incremental" in modules
    assert "src.core.indicators.registry" in modules
    assert len(_MODULE.read_text(encoding="utf-8").splitlines()) <= 300


# ---- (2) 레지스트리 버전 고정 ----


def test_registry_version_is_registry_hash_and_matches_script_hash_input() -> None:
    ta = TaBuiltins()
    assert ta.registry_version == DEFAULT_REGISTRY.registry_hash()
    ir = lower_program(parse(DECLS + "let m = ta.sma(close, 5)"))
    assert script_hash(source="x", ir=ir, registry_version=ta.registry_version) == script_hash(
        source="x", ir=ir, registry_version=DEFAULT_REGISTRY.registry_hash()
    )
    run("let m = ta.sma(close, 5)\nlet e = ta.ema(close)", builtins=ta.table)
    assert [c.registry_hash for c in ta.calls] == [ta.registry_version] * 2
    assert ta.calls[1].params == (("timeperiod", 20),) and ta.calls[1].lookback == 19


def test_mismatched_registry_version_is_rejected_before_any_call() -> None:
    with pytest.raises(BuiltinCallError) as info:
        TaBuiltins(expected_registry_version="0" * 64)
    assert info.value.reason == "INDICATOR_REGISTRY_MISMATCH"
    with pytest.raises(BuiltinCallError):
        default_builtins(registry_version="stale")
    assert ("ta", "sma") in default_builtins(registry_version=DEFAULT_REGISTRY.registry_hash())


def test_custom_registry_changes_hash_and_available_idents() -> None:
    subset = IndicatorRegistry({"SMA": TALIB_SPECS["SMA"]})
    ta = TaBuiltins(subset)
    assert ta.registry_version == subset.registry_hash() != DEFAULT_REGISTRY.registry_hash()
    assert set(ta.table) == {("ta", "sma")}
    with pytest.raises(ScriptRuntimeError, match="미등록"):
        run("let r = ta.rsi(close, 5)", builtins=ta.table)


# ---- (3) 호출 규약 ----


def test_idents_are_derived_from_registry_including_multi_output() -> None:
    idents = {ident for _, ident in TaBuiltins().table}
    assert {n.lower() for n in TALIB_SPECS} <= idents
    multi = {"macd_signal", "macd_hist", "bbands_upperband", "bbands_lowerband", "stoch_slowd"}
    assert multi <= idents
    result = run(
        "let a = ta.macd(close, 3, 6, 4)\nlet s = ta.macd_signal(close, 3, 6, 4)\n"
        "let h = ta.macd_hist(close, 3, 6, 4)\nlet u = ta.bbands_upperband(close, 4)"
    )
    p = {"fastperiod": 3, "slowperiod": 6, "signalperiod": 4}
    assert_close(result.bindings["a"], engine_series("MACD", p, "macd"))
    assert_close(result.bindings["s"], engine_series("MACD", p, "signal"))
    assert_close(result.bindings["h"], engine_series("MACD", p, "hist"))
    assert_close(result.bindings["u"], engine_series("BBANDS", {"timeperiod": 4}, "upperband"))


def test_multi_input_indicator_takes_inputs_in_spec_order_then_params() -> None:
    result = run("let a = ta.atr(high, low, close, 5)\nlet f = ta.mfi(high, low, close, volume, 6)")
    assert_close(result.bindings["a"], engine_series("ATR", {"timeperiod": 5}))
    assert_close(result.bindings["f"], engine_series("MFI", {"timeperiod": 6}))


def test_leading_na_prefix_is_skipped_and_output_is_shifted() -> None:
    direct = run("let m = ta.sma(close, 3)").bindings["m"]
    shifted = run("let m = ta.sma(close[4], 3)").bindings["m"]
    assert isinstance(direct, Series) and isinstance(shifted, Series)
    assert shifted.values[:4] == (None,) * 4
    assert_close(Series(shifted.values[4:]), Series(direct.values[: N - 4]))


def test_result_is_deterministic_and_scalar_input_is_broadcast() -> None:
    body = "let m = ta.ema(close, 4)"
    assert run(body).bindings["m"] == run(body).bindings["m"]
    const = run("let m = ta.sma(7, 3)", bars=5).bindings["m"]
    assert const == Series((None, None, 7.0, 7.0, 7.0))


# ---- (4) negative ----


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("let m = ta.sma(close, 1)", "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("let m = ta.sma(close, 501)", "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("let m = ta.sma(close, 2.5)", "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("let m = ta.sma(close, close)", "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("let m = ta.macd(close, 26, 12, 9)", "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("let m = ta.sma(close, 5, 5)", "SCRIPT_BUILTIN_ARITY"),
        ("let m = ta.atr(close, 5)", "SCRIPT_BUILTIN_ARITY"),
        ("let m = ta.sma(close, 41)", "INDICATOR_LOOKBACK_INSUFFICIENT"),
        ("let m = ta.sma(close[39], 2)", "INDICATOR_LOOKBACK_INSUFFICIENT"),
    ],
)
def test_bad_calls_are_rejected_with_error_code_not_fallback(body: str, reason: str) -> None:
    with pytest.raises(BuiltinCallError) as info:
        run(body)
    assert info.value.reason == reason


def test_unknown_indicator_is_unregistered_not_guessed() -> None:
    with pytest.raises(ScriptRuntimeError, match="미등록"):
        run("let m = ta.supertrend(close, 5)")
    table = TaBuiltins().table
    assert ("ta", "supertrend") not in table and ("math", "abs") not in table


def test_interior_na_bool_and_length_mismatch_inputs_are_rejected() -> None:
    sma = TaBuiltins().table[("ta", "sma")]
    site = CallSite("ta", "sma", "series<float>", 6)
    with pytest.raises(BuiltinCallError) as info:
        sma((Series.of_floats([1, 2, None, 4, 5, 6]), 2), site)
    assert info.value.reason == "INDICATOR_INPUT_INVALID"
    with pytest.raises(BuiltinCallError) as info:
        sma((Series.of_bools([True] * 6), 2), site)
    assert info.value.reason == "INDICATOR_INPUT_INVALID"
    with pytest.raises(BuiltinCallError) as info:
        sma((Series.of_floats([1, 2, 3]), 2), site)
    assert info.value.reason == "INDICATOR_INPUT_INVALID"
    with pytest.raises(BuiltinCallError) as info:
        sma((Series.of_floats([1, 2, 3, 4, 5, 6]), True), site)
    assert info.value.reason == "STRATEGY_PARAM_OUT_OF_RANGE"
    with pytest.raises(BuiltinCallError) as info:
        sma((Series.of_floats([None] * 6), 2), site)
    assert info.value.reason == "INDICATOR_LOOKBACK_INSUFFICIENT"
