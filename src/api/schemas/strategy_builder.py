"""14번 — 전략 편집기 API 요청·응답 스키마.

편차: 16_backend_signatures.md §16.4 Draft는 진입/청산 조건 각 1개
(ConditionSpec 단수)만 가정했지만, 실제 구현된 ConditionCompiler/
PreviewCalculator는 진입/청산/손절 조건 각각을 리스트+AND/OR 결합으로
받는다(services/condition_compiler.py, services/preview_service.py 참조,
이미 이 세션에서 완성돼 테스트까지 통과한 실제 계약) — 여기서는 Draft가
아니라 그 실제 서비스 시그니처에 맞춘다.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from src.services.preview_service import PreviewCondition
from src.services.strategy_builder_service import SavedStrategy, StrategyDetail


class IndicatorListResponse(BaseModel):
    indicators: list[str]


class IndicatorComputeResponse(BaseModel):
    indicator: str
    values: list[float | None]
    series: dict[str, list[float | None]] | None = None
    params: dict[str, int]
    message: str | None = None


class StrategyCreateRequest(BaseModel):
    strategy_id: str
    version: str
    target_asset: str
    market: str
    exchange: str
    entry_conditions: list[PreviewCondition]
    exit_conditions: list[PreviewCondition]
    stop_loss_conditions: list[PreviewCondition]
    entry_combine: str = "AND"
    exit_combine: str = "AND"
    stop_loss_combine: str = "AND"


class StrategyResponse(BaseModel):
    strategy_id: str
    version: str
    status: str
    fsm_definition: dict[str, Any]


def to_strategy_response(saved: SavedStrategy, fsm_definition: dict[str, Any]) -> StrategyResponse:
    return StrategyResponse(
        strategy_id=saved.strategy_id,
        version=saved.version,
        status=saved.lifecycle_status,
        fsm_definition=fsm_definition,
    )


class StrategyDetailResponse(BaseModel):
    strategy_id: str
    version: str
    target_asset: str
    market: str
    exchange: str
    status: str
    fsm_definition: dict[str, Any]


def to_strategy_detail_response(detail: StrategyDetail) -> StrategyDetailResponse:
    return StrategyDetailResponse(
        strategy_id=detail.strategy_id,
        version=detail.version,
        target_asset=detail.target_asset,
        market=detail.market,
        exchange=detail.exchange,
        status=detail.lifecycle_status,
        fsm_definition=detail.fsm_definition,
    )


class PreviewRequest(BaseModel):
    exchange: str
    symbol: str
    timeframe: str = "1h"
    limit: int = 200
    conditions: list[PreviewCondition]
    combine: Literal["AND", "OR"] = "AND"


class PreviewResponse(BaseModel):
    signal_indices: list[int]
    signal_times: list[str]
    disclaimer: str
    message: str | None = None


class WizardGenerateRequest(BaseModel):
    goal: str
    risk_tolerance: str


class PromptGenerateRequest(BaseModel):
    prompt: str
