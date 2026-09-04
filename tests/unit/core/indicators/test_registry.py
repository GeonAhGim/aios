"""L01 — spec.py / specs_talib.py 계약 테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01, DoD:
`pytest tests/unit/core/indicators/test_registry.py -k specs` — 11개 스펙의
lookback이 실제 TA-Lib 출력의 선행 NaN 개수와 실측으로 일치해야 한다
(하드코딩 기대값과의 동어반복 비교는 금지).

registry.py(조회·검증 단일 진입점)는 L02 몫이라 아직 없다 — 여기서는
`TALIB_SPECS`/`ParamSpec`의 범위 값 자체를 직접 대조해 "미지 지표"·
"범위 밖 파라미터" negative case를 검증한다.
"""
from __future__ import annotations

import numpy as np
import pytest
import talib

from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec, ParamSpec
from src.core.indicators.specs_talib import TALIB_SPECS

EXPECTED_INDICATORS = frozenset(
    {"SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "MACD", "BBANDS", "STOCH", "OBV"}
)


def _default_params(spec: IndicatorSpec) -> dict[str, int]:
    return {p.name: p.default for p in spec.params}


def _synthetic_ohlcv(n: int = 300) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.normal(size=n)) + 100.0
    high = close + np.abs(rng.normal(size=n)) + 0.5
    low = close - np.abs(rng.normal(size=n)) - 0.5
    volume = np.abs(rng.normal(size=n)) * 1000.0 + 100.0
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def _leading_nan_count(arr: np.ndarray) -> int:
    valid = np.where(~np.isnan(arr))[0]
    return int(valid[0]) if len(valid) else len(arr)


def test_specs_registry_version_is_ind_v1() -> None:
    assert REGISTRY_VERSION == "ind-v1"


def test_specs_cover_exactly_eleven_talib_indicators() -> None:
    assert set(TALIB_SPECS) == EXPECTED_INDICATORS
    assert len(TALIB_SPECS) == 11


@pytest.mark.parametrize("name", sorted(EXPECTED_INDICATORS))
def test_specs_lookback_matches_talib_nan_count(name: str) -> None:
    spec = TALIB_SPECS[name]
    arrays = _synthetic_ohlcv()
    inputs = [arrays[key] for key in spec.inputs]
    params = _default_params(spec)

    talib_func = getattr(talib, name)
    raw_output = talib_func(*inputs, **params)
    first_line = raw_output[0] if isinstance(raw_output, tuple) else raw_output

    actual_leading_nan = _leading_nan_count(first_line)
    assert spec.lookback(params) == actual_leading_nan


@pytest.mark.parametrize("name", sorted(EXPECTED_INDICATORS))
def test_specs_defaults_are_within_param_range(name: str) -> None:
    spec = TALIB_SPECS[name]
    for param in spec.params:
        assert param.min <= param.default <= param.max
    assert spec.causal is True
    assert len(spec.outputs) >= 1


def test_specs_unknown_indicator_is_rejected() -> None:
    assert "UNKNOWN_INDICATOR" not in TALIB_SPECS
    assert TALIB_SPECS.get("ICHIMOKU") is None


def test_specs_out_of_range_param_is_rejected() -> None:
    spec = TALIB_SPECS["SMA"]
    timeperiod = next(p for p in spec.params if p.name == "timeperiod")
    assert timeperiod.min == 2
    assert timeperiod.max == 500
    assert not (timeperiod.min <= 1 <= timeperiod.max)
    assert not (timeperiod.min <= 501 <= timeperiod.max)


def test_param_spec_is_frozen() -> None:
    spec = ParamSpec(name="timeperiod", min=2, max=500, default=20)
    with pytest.raises(AttributeError):
        spec.default = 30  # type: ignore[misc]
