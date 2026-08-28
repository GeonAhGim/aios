"""15번대 — 적합성평가 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.services.risk_profile_service import RiskProfileService
from src.services.suitability_questionnaire import SuitabilityQuestionnaire

from .deps import get_pool


def get_suitability_questionnaire() -> SuitabilityQuestionnaire:
    return SuitabilityQuestionnaire()


def get_risk_profile_service(pool: asyncpg.Pool = Depends(get_pool)) -> RiskProfileService:
    return RiskProfileService(pool)
