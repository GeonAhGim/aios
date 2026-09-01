"""14.4 — 프리뷰 계산기 (경량 미리보기).

Spec: 기능설계문서_v1.20.md#FD-14.4

정식 백테스트(9.3)가 아니다 — 저장 없이 즉석으로 지표 조건을 최근
캔들에 적용해 신호가 발생했을 시점만 보여주는 가벼운 미리보기.
FD-14.2(정식 조건 조합 UI + FSM 컴파일)는 프론트엔드 영역이라 이 세션
스콥 밖 — 여기서는 그보다 단순한 형태(지표+비교연산자+임계값 목록을
AND/OR로 결합)만 지원한다.

이 계산기가 만드는 신호는 FD-8(FROZEN, 실제 매매판단)과 전혀 무관하다 —
이 결과로 실제 주문이 나가는 경로는 어디에도 없다.
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
