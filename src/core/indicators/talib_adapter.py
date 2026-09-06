"""14.1 — 기술적 지표 라이브러리 연동 (IndicatorService).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L03,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10

TA-Lib를 캔들 데이터(FD-2 파이프라인 산출물, Candle 모델)에 적용해
시계열 지표값을 계산하는 순수 계산 계층 — FD-8(FROZEN, 실제 매매판단)의
경계를 넘지 않는다. 파라미터 검증·lookback 산정은 L01/L02
(`specs_talib.py`/`registry.py`)에 전부 위임한다 — §1 결함(구 지표별
스펙 딕셔너리는 범위 검증이 없어 timeperiod=0·음수가 통과했고, lookback이
period 파라미터 하나뿐이라 MACD/BBANDS/STOCH의 실제 필요 bar 수와
불일치했다)을 여기서 재구현하지 않고 `DEFAULT_REGISTRY`로 대체해 고친다.

IND-10(161종 자동 생성) 이후 `SUPPORTED_INDICATORS`는 손으로 나열한 11개가
아니라 `TALIB_SPECS`(자동 생성 150 + 수기 오버라이드 11) 전체를 반영한다.
`INDICATOR_GROUPS`는 TA-Lib 자체 10개 그룹(`TALIB_GROUPS`)으로 묶은 조회용
뷰다 — 예전의 TREND/MOMENTUM/VOLATILITY/VOLUME 4분류는 161종을 설명하기엔
너무 성겼다(예: Momentum Indicators만 31종).

범위 축소: Ichimoku/Keltner Channel/VWAP/Fibonacci Retracement/Pivot
Points는 TA-Lib 표준 함수 집합에 아예 없다(자체 공식이 필요한 별도
구현 대상) — 완료조건이 요구하는 "TA-Lib 참조값과 일치" 자체를 검증할
수 없는 지표라 이번 leaf에서는 다루지 않는다. `MAVP`는 TA-Lib 표준
함수지만 두 번째 입력이 캔들 필드가 아니라 가변 주기 배열(`periods`)이라
`Candle` 기반 어댑터가 공급할 수 없다 — 레지스트리에는 등록되지만
`calculate()` 호출 시 `STRATEGY_INDICATOR_INPUT_UNSUPPORTED`로 거부한다.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import numpy as np
import talib
from pydantic import BaseModel

from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError
from src.core.indicators.spec import REGISTRY_VERSION
from src.core.indicators.specs_talib import TALIB_GROUPS, TALIB_SPECS
from src.data.models.market_data import Candle

SUPPORTED_INDICATORS = tuple(sorted(TALIB_SPECS))


def _group_indicators() -> dict[str, tuple[str, ...]]:
    by_group: dict[str, list[str]] = defaultdict(list)
    for name in SUPPORTED_INDICATORS:
        by_group[TALIB_GROUPS[name]].append(name)
    return {group: tuple(sorted(names)) for group, names in by_group.items()}


INDICATOR_GROUPS = _group_indicators()

__all__ = [
    "INDICATOR_GROUPS",
    "SUPPORTED_INDICATORS",
    "IndicatorError",
    "IndicatorResult",
    "IndicatorService",
]


class IndicatorResult(BaseModel):
    indicator: str
    values: list[float | None]
    series: dict[str, list[float | None]] | None = None
    params: dict[str, int]
    message: str | None = None
    registry_version: str = REGISTRY_VERSION


def _candle_arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray[Any, Any]]:
    return {
        "open": np.array([float(c.open) for c in candles], dtype=np.float64),
        "high": np.array([float(c.high) for c in candles], dtype=np.float64),
        "low": np.array([float(c.low) for c in candles], dtype=np.float64),
        "close": np.array([float(c.close) for c in candles], dtype=np.float64),
        "volume": np.array([float(c.volume) for c in candles], dtype=np.float64),
    }


def _clean(arr: np.ndarray[Any, Any]) -> list[float | None]:
    """TA-Lib 출력을 None(결측)/float 리스트로 정리한다.

    캔들 패턴(CDL*) 61종은 int32 배열을 반환하는데 `np.isnan`은 정수 dtype에
    쓸 수 없다(TypeError) — float64로 캐스팅 후 처리한다(정수 배열은 애초에
    NaN을 가질 수 없으므로 값 손실 없음).
    """
    floats = arr.astype(np.float64, copy=False)
    return [None if np.isnan(v) else float(v) for v in floats]


class IndicatorService:
    """캔들 → 지표값. 지표 조회·파라미터 검증·lookback은 `DEFAULT_REGISTRY`
    (L02)에 위임하고, 여기서는 TA-Lib 호출과 결과 포장만 한다."""

    def calculate(
        self, indicator: str, candles: Sequence[Candle], **params: int
    ) -> IndicatorResult:
        spec = DEFAULT_REGISTRY.get(indicator)
        resolved_params = DEFAULT_REGISTRY.validate_params(indicator, params)
        min_required = DEFAULT_REGISTRY.lookback(indicator, resolved_params) + 1

        if len(candles) < min_required:
            return IndicatorResult(
                indicator=indicator,
                values=[],
                params=resolved_params,
                message=f"데이터 부족, 최소 {min_required}개 필요",
            )

        arrays = _candle_arrays(candles)
        missing_inputs = [name for name in spec.inputs if name not in arrays]
        if missing_inputs:
            raise IndicatorError("STRATEGY_INDICATOR_INPUT_UNSUPPORTED")
        inputs = [arrays[name] for name in spec.inputs]
        talib_func = getattr(talib, indicator)
        raw_output = talib_func(*inputs, **resolved_params)

        if len(spec.outputs) == 1:
            return IndicatorResult(
                indicator=indicator, values=_clean(raw_output), params=resolved_params
            )

        series = {
            name: _clean(line) for name, line in zip(spec.outputs, raw_output, strict=True)
        }
        primary = series[spec.outputs[0]]
        return IndicatorResult(
            indicator=indicator, values=primary, series=series, params=resolved_params
        )
