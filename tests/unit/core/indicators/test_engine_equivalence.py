"""IND-1 — 증분(`engine/incremental.py`) = 일괄(`engine/vectorized.py`) 동일성 property 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.3 IND-1
DoD: 레지스트리 등재 지표 전종에 대해 증분 갱신 결과와 일괄 계산 결과가 1e-9 이내
동일, lookback 미충족 구간은 None/NaN 명시 반환(0 대체 금지).

property 스타일이지만 hypothesis 없이 시드 고정 RNG로 (지표 × 시드) 격자를 돌린다 —
파라미터는 L01 `ParamSpec` 범위에서, 길이·가격 스케일은 여러 자릿수에 걸쳐 뽑아
결정론적으로 재현된다. 핵심 테스트는 TA-Lib을 import하지 않는다(레지스트리 스펙만
소비). TA-Lib 참조 대조는 마지막 테스트 하나에서 함수 내부 import로만 쓴다 —
미설치면 skip이 아니라 실패한다(fail-closed, 저장소는 이미 talib에 하드 의존).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from src.core.indicators.engine import vectorized
from src.core.indicators.engine.incremental import IncrementalIndicator
from src.core.indicators.engine.vectorized import (
    EQUIVALENCE_TOLERANCE,
    check_equivalence,
    compute,
    run_incremental,
)
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError
from src.core.indicators.spec import IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS

NAMES = sorted(TALIB_SPECS)
SEEDS = list(range(6))
SCALES = (1.0, 100.0, 1e5)


def _random_params(spec: IndicatorSpec, rng: np.random.Generator) -> dict[str, int]:
    """ParamSpec 범위 안에서 뽑되 작은 값 쪽을 자주 뽑는다(짧은 시리즈에서도 값이 나오게)."""
    params = {
        p.name: int(min(p.max, p.min + rng.integers(0, 40) * (1 if rng.random() < 0.8 else 12)))
        for p in spec.params
    }
    if spec.name == "MACD" and params["fastperiod"] >= params["slowperiod"]:
        params["fastperiod"], params["slowperiod"] = (
            min(params["fastperiod"], params["slowperiod"]),
            max(params["fastperiod"], params["slowperiod"]) + 1,
        )
    return params


def _ohlcv(rng: np.random.Generator, n: int, scale: float) -> dict[str, np.ndarray]:
    close = scale * (1.0 + 0.01 * np.cumsum(rng.normal(size=n)))
    close = np.maximum(close, scale * 0.05)
    spread = scale * 0.005 * np.abs(rng.normal(size=n))
    high = close + spread + scale * 0.001
    low = close - spread - scale * 0.001
    volume = np.abs(rng.normal(size=n)) * 1000.0 + 1.0
    # 같은 종가가 반복되는 구간을 섞어 diff == 0 분기(OBV·RSI·MFI)도 지나가게 한다
    flat = rng.integers(0, n, size=max(1, n // 10))
    close[flat[flat > 0]] = close[flat[flat > 0] - 1]
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def _case(name: str, seed: int) -> tuple[dict[str, int], dict[str, np.ndarray], int]:
    rng = np.random.default_rng(seed * 1009 + NAMES.index(name))
    params = _random_params(TALIB_SPECS[name], rng)
    lookback = DEFAULT_REGISTRY.lookback(name, params)
    n = lookback + int(rng.integers(1, 120))
    return params, _ohlcv(rng, n, SCALES[seed % len(SCALES)]), lookback


def _stream(name: str, params: dict[str, int], cols: dict[str, np.ndarray]) -> list[dict]:
    ind = IncrementalIndicator(name, params)
    keys = ind.spec.inputs
    return [ind.update({k: float(cols[k][i]) for k in keys}) for i in range(len(cols["close"]))]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", NAMES)
def test_incremental_equals_vectorized_within_1e9(name: str, seed: int) -> None:
    params, cols, lookback = _case(name, seed)
    batch = compute(name, cols, params)
    streamed = _stream(name, params, cols)
    for key, arr in batch.items():
        for i, row in enumerate(streamed):
            value = row[key]
            if i < lookback:
                assert value is None and np.isnan(arr[i]), (name, key, i)
                continue
            assert value is not None and np.isfinite(arr[i]), (name, key, i)
            scale = max(1.0, abs(value), abs(float(arr[i])))
            assert abs(value - float(arr[i])) / scale <= EQUIVALENCE_TOLERANCE, (name, key, i)
    assert check_equivalence(name, cols, params) <= EQUIVALENCE_TOLERANCE


@pytest.mark.parametrize("name", NAMES)
def test_lookback_prefix_is_nan_not_zero_and_matches_registry(name: str) -> None:
    for seed in SEEDS[:3]:
        params, cols, lookback = _case(name, seed)
        assert lookback == DEFAULT_REGISTRY.lookback(name, params)
        for arr in compute(name, cols, params).values():
            assert np.all(np.isnan(arr[:lookback]))
            assert not np.any(arr[:lookback] == 0.0)
            assert np.all(np.isfinite(arr[lookback:]))
        streamed = _stream(name, params, cols)
        assert all(v is None for row in streamed[:lookback] for v in row.values())
        assert all(v is not None for row in streamed[lookback:] for v in row.values())


@pytest.mark.parametrize("name", NAMES)
def test_series_shorter_than_lookback_yields_only_nan_and_none(name: str) -> None:
    params = {p.name: p.default for p in TALIB_SPECS[name].params}
    lookback = DEFAULT_REGISTRY.lookback(name, params)
    if lookback == 0:
        return
    cols = _ohlcv(np.random.default_rng(1), lookback, 100.0)
    assert all(np.isnan(a).all() for a in compute(name, cols, params).values())
    assert all(v is None for row in _stream(name, params, cols) for v in row.values())


@pytest.mark.parametrize("name", NAMES)
def test_vectorized_is_causal_prefix_stable(name: str) -> None:
    """causal 지표: 앞 m개 bar만으로 계산한 값이 전체 시리즈 계산의 앞 m개와 같다."""
    params, cols, lookback = _case(name, 2)
    assert TALIB_SPECS[name].causal
    full = compute(name, cols, params)
    m = lookback + 3
    prefix = compute(name, {k: v[:m] for k, v in cols.items()}, params)
    for key in full:
        np.testing.assert_allclose(prefix[key], full[key][:m], rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize("name", NAMES)
def test_engines_are_deterministic_across_runs(name: str) -> None:
    params, cols, _ = _case(name, 4)
    first, second = compute(name, cols, params), compute(name, cols, params)
    for key in first:
        assert np.array_equal(first[key], second[key], equal_nan=True)
    a, b = run_incremental(name, cols, params), run_incremental(name, cols, params)
    for key in a:
        assert np.array_equal(a[key], b[key], equal_nan=True)


def test_decimal_inputs_are_accepted_by_both_engines() -> None:
    cols = _ohlcv(np.random.default_rng(7), 30, 100.0)
    dec = {k: [Decimal(str(round(float(x), 6))) for x in v] for k, v in cols.items()}
    assert check_equivalence("SMA", dec, {"timeperiod": 5}) <= EQUIVALENCE_TOLERANCE
    ind = IncrementalIndicator("OBV")
    assert ind.update({"close": Decimal("1.5"), "volume": Decimal("10")}) == {"value": 10.0}


# ---------------------------------------------------------------- negative --


@pytest.mark.parametrize(
    ("name", "params", "code"),
    [
        ("NOPE", {}, "STRATEGY_INDICATOR_UNKNOWN"),
        ("SMA", {"timeperiod": 1}, "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("SMA", {"timeperiod": 501}, "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("RSI", {"timeperiod": True}, "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("MACD", {"fastperiod": 26, "slowperiod": 12}, "STRATEGY_PARAM_OUT_OF_RANGE"),
        ("MACD", {"fastperiod": 12, "slowperiod": 12}, "STRATEGY_PARAM_OUT_OF_RANGE"),
    ],
)
def test_both_engines_reject_bad_requests(name: str, params: dict[str, Any], code: str) -> None:
    cols = _ohlcv(np.random.default_rng(0), 50, 100.0)
    with pytest.raises(IndicatorError) as exc:
        compute(name, cols, params)
    assert exc.value.code == code
    with pytest.raises(IndicatorError) as exc:
        IncrementalIndicator(name, params)
    assert exc.value.code == code


def test_vectorized_rejects_missing_column_length_mismatch_nan_and_empty() -> None:
    cols = _ohlcv(np.random.default_rng(0), 40, 100.0)
    for bad in (
        {"close": cols["close"]},  # ATR needs high/low
        {**cols, "high": cols["high"][:-1]},
        {**cols, "low": np.where(np.arange(40) == 5, np.nan, cols["low"])},
        {**cols, "close": np.where(np.arange(40) == 5, np.inf, cols["close"])},
        {k: v[:0] for k, v in cols.items()},
        {**cols, "close": [[1.0, 2.0]] * 40},
    ):
        with pytest.raises(IndicatorError) as exc:
            compute("ATR", bad)
        assert exc.value.code == "INDICATOR_INPUT_INVALID"


def test_incremental_rejects_missing_nan_inf_and_bool_inputs() -> None:
    ind = IncrementalIndicator("ATR")
    for bad in (
        {"high": 1.0, "low": 0.5},
        {"high": float("nan"), "low": 0.5, "close": 0.7},
        {"high": float("inf"), "low": 0.5, "close": 0.7},
        {"high": True, "low": 0.5, "close": 0.7},
        {"high": "1.0", "low": 0.5, "close": 0.7},
    ):
        with pytest.raises(IndicatorError) as exc:
            ind.update(bad)  # type: ignore[arg-type]
        assert exc.value.code == "INDICATOR_INPUT_INVALID"
    assert ind.bars_seen == 0


def test_check_equivalence_detects_engine_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """계약 검사기가 공허하지 않음: 일괄 커널이 2e-9만 어긋나도 잡는다."""
    cols = _ohlcv(np.random.default_rng(3), 60, 100.0)
    original = vectorized._KERNELS["SMA"]

    def drifted(c: dict[str, Any], p: dict[str, int]) -> tuple[Any, ...]:
        (out,) = original(c, p)
        return (out * (1.0 + 2e-9),)

    monkeypatch.setitem(vectorized._KERNELS, "SMA", drifted)
    with pytest.raises(IndicatorError) as exc:
        check_equivalence("SMA", cols, {"timeperiod": 10})
    assert exc.value.code == "INDICATOR_ENGINE_MISMATCH"


def test_vectorized_refuses_output_whose_nan_prefix_disagrees_with_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cols = _ohlcv(np.random.default_rng(3), 60, 100.0)
    original = vectorized._KERNELS["SMA"]

    def one_bar_early(c: dict[str, Any], p: dict[str, int]) -> tuple[Any, ...]:
        (out,) = original(c, p)
        out = out.copy()
        out[p["timeperiod"] - 2] = 0.0  # lookback 자리에 0을 채워 넣는 위조
        return (out,)

    monkeypatch.setitem(vectorized._KERNELS, "SMA", one_bar_early)
    with pytest.raises(IndicatorError) as exc:
        compute("SMA", cols, {"timeperiod": 10})
    assert exc.value.code == "INDICATOR_LOOKBACK_MISMATCH"


# ------------------------------------------------------- TA-Lib reference --


@pytest.mark.parametrize("name", NAMES)
def test_vectorized_matches_talib_reference_default_params(name: str) -> None:
    """L01 lookback의 출처인 TA-Lib과 값 자체도 맞는지(기본 파라미터, 1e-6 스케일 편차)."""
    import talib

    spec = TALIB_SPECS[name]
    params = {p.name: p.default for p in spec.params}
    cols = _ohlcv(np.random.default_rng(11), 300, 100.0)
    ours = compute(name, cols, params)
    raw = getattr(talib, name)(*[cols[k] for k in spec.inputs], **params)
    reference = dict(zip(spec.outputs, raw if len(spec.outputs) > 1 else (raw,), strict=True))
    for key in spec.outputs:
        a, b = ours[key], reference[key]
        assert np.array_equal(np.isnan(a), np.isnan(b)), (name, key)
        finite = ~np.isnan(a)
        scale = np.maximum(1.0, np.abs(b[finite]))
        assert np.max(np.abs(a[finite] - b[finite]) / scale) <= 1e-6, (name, key)
