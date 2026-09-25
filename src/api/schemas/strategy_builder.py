"""Strategy editor API request/response schemas (Task 14).

Deviation: 16_backend_signatures.md §16.4 Draft assumes a single
ConditionSpec each for entry and exit conditions, but the implemented
ConditionCompiler/PreviewCalculator accept lists of entry/exit/stop-loss
conditions combined with AND/OR logic (see services/condition_compiler.py,
services/preview_service.py — an actual contract completed this session and
tested). This module follows the real service signatures, not the Draft.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from src.data.models.market_data import Candle
from src.services.preview_service import PreviewCondition
from src.services.strategy_builder_service import SavedStrategy, StrategyDetail


class CandleResponse(BaseModel):
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def to_candle_response(candle: Candle) -> CandleResponse:
    return CandleResponse(
        open_time=candle.open_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


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
