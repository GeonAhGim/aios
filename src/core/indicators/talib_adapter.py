"""14.1 — 기술적 지표 라이브러리 연동 (IndicatorService).

Spec: 기능설계문서_v1.20.md#FD-14.1, FD-2(시장데이터), 9.11

TA-Lib를 캔들 데이터(FD-2 파이프라인 산출물, Candle 모델)에 적용해
시계열 지표값을 계산하는 순수 계산 계층 — FD-8(FROZEN, 실제 매매판단)의
경계를 넘지 않는다. 지표별 필요 캔들 수(예: MA(200))가 부족하면 오류
대신 안내 메시지와 함께 빈 배열을 반환한다(FD-14.1 예외상황).

범위 축소: Ichimoku/Keltner Channel/VWAP/Fibonacci Retracement/Pivot
Points는 TA-Lib 표준 함수 집합에 아예 없다(자체 공식이 필요한 별도
구현 대상) — 완료조건이 요구하는 "TA-Lib 참조값과 일치" 자체를 검증할
수 없는 지표라 이번 leaf에서는 다루지 않는다(12.2/13.8과 동일하게
정직하게 범위를 축소). 4개 지표군(추세/모멘텀/변동성/거래량) 각각에서
TA-Lib가 실제 제공하는 대표 지표를 구현해 완료조건을 충족한다.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import talib
from pydantic import BaseModel

from src.data.models.market_data import Candle

TREND_INDICATORS = ("SMA", "EMA", "MACD")
MOMENTUM_INDICATORS = ("RSI", "STOCH", "CCI", "WILLR")
VOLATILITY_INDICATORS = ("BBANDS", "ATR")
VOLUME_INDICATORS = ("OBV", "MFI")

SUPPORTED_INDICATORS = (
    TREND_INDICATORS + MOMENTUM_INDICATORS + VOLATILITY_INDICATORS + VOLUME_INDICATORS
)

# 지표별: (필요 입력 시리즈, 캔들 수 판정에 쓸 파라미터명, 기본 period, 다중출력 라인 이름)
_SPECS: dict[str, dict[str, Any]] = {
    "SMA": {"inputs": ("close",), "period_param": "timeperiod", "default_period": 20},
    "EMA": {"inputs": ("close",), "period_param": "timeperiod", "default_period": 20},
    "MACD": {
        "inputs": ("close",),
        "period_param": "slowperiod",
        "default_period": 26,
        "output_names": ("macd", "signal", "hist"),
    },
    "RSI": {"inputs": ("close",), "period_param": "timeperiod", "default_period": 14},
    "STOCH": {
        "inputs": ("high", "low", "close"),
        "period_param": "fastk_period",
        "default_period": 5,
        "output_names": ("slowk", "slowd"),
    },
    "CCI": {"inputs": ("high", "low", "close"), "period_param": "timeperiod", "default_period": 14},
    "WILLR": {
        "inputs": ("high", "low", "close"),
        "period_param": "timeperiod",
        "default_period": 14,
    },
    "BBANDS": {
        "inputs": ("close",),
        "period_param": "timeperiod",
        "default_period": 5,
        "output_names": ("upperband", "middleband", "lowerband"),
    },
    "ATR": {"inputs": ("high", "low", "close"), "period_param": "timeperiod", "default_period": 14},
    "OBV": {"inputs": ("close", "volume"), "period_param": None, "default_period": 1},
    "MFI": {
        "inputs": ("high", "low", "close", "volume"),
        "period_param": "timeperiod",
        "default_period": 14,
    },
}


class IndicatorError(Exception):
    """지원하지 않는 지표 요청 — 라우터가 400으로 변환."""


class IndicatorResult(BaseModel):
    indicator: str
    values: list[float | None]
    series: dict[str, list[float | None]] | None = None
    params: dict[str, int]
    message: str | None = None


def _candle_arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray[Any, Any]]:
    return {
        "open": np.array([float(c.open) for c in candles], dtype=np.float64),
        "high": np.array([float(c.high) for c in candles], dtype=np.float64),
        "low": np.array([float(c.low) for c in candles], dtype=np.float64),
        "close": np.array([float(c.close) for c in candles], dtype=np.float64),
        "volume": np.array([float(c.volume) for c in candles], dtype=np.float64),
    }


def _clean(arr: np.ndarray[Any, Any]) -> list[float | None]:
    return [None if np.isnan(v) else float(v) for v in arr]


class IndicatorService:
    def calculate(
        self, indicator: str, candles: Sequence[Candle], **params: int
    ) -> IndicatorResult:
        spec = _SPECS.get(indicator)
        if spec is None:
            raise IndicatorError(f"지원하지 않는 지표입니다: {indicator}")

        period_param = spec["period_param"]
        resolved_params: dict[str, int] = dict(params)
        if period_param is not None and period_param not in resolved_params:
            resolved_params[period_param] = spec["default_period"]

        min_required = resolved_params.get(period_param, 1) if period_param else 1
        if len(candles) < min_required:
            return IndicatorResult(
                indicator=indicator,
                values=[],
                params=resolved_params,
                message=f"데이터 부족, 최소 {min_required}개 필요",
            )

        arrays = _candle_arrays(candles)
        inputs = [arrays[name] for name in spec["inputs"]]
        talib_func = getattr(talib, indicator)
        raw_output = talib_func(*inputs, **resolved_params)

        output_names = spec.get("output_names")
        if output_names is None:
            return IndicatorResult(
                indicator=indicator, values=_clean(raw_output), params=resolved_params
            )

        series = {name: _clean(line) for name, line in zip(output_names, raw_output, strict=True)}
        primary = series[output_names[0]]
        return IndicatorResult(
            indicator=indicator, values=primary, series=series, params=resolved_params
        )
