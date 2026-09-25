"""14.4 — Preview calculator (lightweight preview).

Spec: 기능설계문서_v1.20.md#FD-14.4

Not a full backtest (9.3) — a lightweight preview that applies indicator
conditions to recent candles on-the-fly without saving, showing only
the timepoints where a signal would fire.
FD-14.2 (full condition-combination UI + FSM compile) is a frontend
concern and out of scope here — we support a simpler form only
(indicator + comparison operator + threshold list combined via AND/OR).

Signals produced by this calculator are unrelated to FD-8 (FROZEN,
actual trade decisions) — no code path places real orders from these
results.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle
from src.services.condition_evaluation import Operator, compare_value

DISCLAIMER = "이것은 정식 백테스트가 아닙니다 — 조건의 대략적인 작동만 보여줍니다."


class PreviewCondition(BaseModel):
    indicator: str
    params: dict[str, int] = {}
    operator: Operator
    threshold: float


class PreviewResult(BaseModel):
    signal_indices: list[int]
    signal_times: list[str]
    disclaimer: str = DISCLAIMER
    message: str | None = None


class PreviewCalculator:
    def __init__(self, indicator_service: IndicatorService | None = None) -> None:
        self._indicators = indicator_service or IndicatorService()

    def preview(
        self,
        candles: list[Candle],
        conditions: list[PreviewCondition],
        *,
        combine: Literal["AND", "OR"] = "AND",
    ) -> PreviewResult:
        if not conditions:
            return PreviewResult(signal_indices=[], signal_times=[])

        series_by_condition: list[tuple[PreviewCondition, list[float | None]]] = []
        for condition in conditions:
            result = self._indicators.calculate(
                condition.indicator, candles, **condition.params
            )
            if not result.values:
                return PreviewResult(signal_indices=[], signal_times=[], message=result.message)
            series_by_condition.append((condition, result.values))

        signal_indices: list[int] = []
        for i in range(len(candles)):
            evaluations = []
            for condition, values in series_by_condition:
                value = values[i]
                if value is None:
                    evaluations.append(False)
                    continue
                prev_value = values[i - 1] if i > 0 else None
                evaluations.append(
                    compare_value(value, condition.operator, condition.threshold, prev_value)
                )
            combined = all(evaluations) if combine == "AND" else any(evaluations)
            if combined:
                signal_indices.append(i)

        signal_times = [candles[i].open_time.isoformat() for i in signal_indices]
        return PreviewResult(signal_indices=signal_indices, signal_times=signal_times)
