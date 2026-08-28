"""19번대 — 포트폴리오 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.api.service_deps import get_risk_policy
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.portfolio_service import PortfolioService

from .deps import get_pool


def get_portfolio_service(
    pool: asyncpg.Pool = Depends(get_pool),
    policy: RiskPolicy = Depends(get_risk_policy),
) -> PortfolioService:
    return PortfolioService(pool, policy)
