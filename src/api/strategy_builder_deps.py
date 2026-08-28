"""14번대 — 전략 편집기 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.core.indicators.talib_adapter import IndicatorService
from src.services.strategy_builder_service import StrategyBuilderService

from .deps import get_pool


def get_indicator_service() -> IndicatorService:
    return IndicatorService()


def get_strategy_builder_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> StrategyBuilderService:
    return StrategyBuilderService(pool)
