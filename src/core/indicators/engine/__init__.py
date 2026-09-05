"""IND-1 — 지표 엔진 공통 계약(증분 `incremental.py` · 일괄 `vectorized.py`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.3, §9.3 IND-1

두 엔진이 공유하는 요청 해석과 입력 검증만 둔다. 지표 조회·파라미터 범위·
lookback은 L02 `IndicatorRegistry`(L01 TA-Lib 실측 lookback) 단일 출처에 위임하고
여기서 재정의하지 않는다. 오류 코드:
- `STRATEGY_INDICATOR_UNKNOWN` / `STRATEGY_PARAM_OUT_OF_RANGE` — L02 그대로.
- `INDICATOR_INPUT_INVALID` — 입력 컬럼 누락·길이 불일치·비유한값(fail-closed).
- `INDICATOR_LOOKBACK_MISMATCH` — 엔진 산출의 NaN 접두 길이가 레지스트리 lookback과 다름.
- `INDICATOR_ENGINE_MISMATCH` — 증분과 일괄 결과가 1e-9 밖(`vectorized.check_equivalence`).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal

from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.indicators.spec import IndicatorSpec

__all__ = ["Bar", "Values", "resolve_request", "validate_input"]

Values = tuple[float, ...]
Bar = Mapping[str, float | int | Decimal]


def resolve_request(
    name: str, params: Mapping[str, int] | None, registry: IndicatorRegistry
) -> tuple[IndicatorSpec, dict[str, int], int]:
    """(spec, 검증된 params, 레지스트리 lookback).

    MACD `fastperiod >= slowperiod`는 L01 lookback 산식(slow 기준)과 실제 필요
    bar 수가 어긋나므로 fail-closed 거부한다(TA-Lib은 두 기간을 맞바꾸지만 그
    경우 lookback이 레지스트리 값과 달라진다).
    """
    spec = registry.get(name)
    resolved = registry.validate_params(name, params or {})
    if name == "MACD" and resolved["fastperiod"] >= resolved["slowperiod"]:
        raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
    return spec, resolved, registry.lookback(name, resolved)


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    number = float(value)
    if not math.isfinite(number):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    return number


def validate_input(spec: IndicatorSpec, bar: Bar) -> dict[str, float]:
    """spec.inputs 전부가 유한한 수로 존재해야 통과. 누락·NaN·inf·bool은 거부."""
    inputs = {key: _finite(bar[key]) for key in spec.inputs if key in bar}
    if len(inputs) != len(spec.inputs):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    return inputs
