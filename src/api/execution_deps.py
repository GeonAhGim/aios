"""16번대 — 실행 제어판 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.api.service_deps import get_risk_policy
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService

from .deps import get_pool


def get_execution_service(
    pool: asyncpg.Pool = Depends(get_pool),
    policy: RiskPolicy = Depends(get_risk_policy),
) -> ExecutionService:
    return ExecutionService(pool, policy)


def get_execution_monitoring_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExecutionMonitoringService:
    return ExecutionMonitoringService(pool)
