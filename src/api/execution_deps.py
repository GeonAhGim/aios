"""16번대 — 실행 제어판 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.api.service_deps import get_risk_policy
from src.core.event_bus.bus import EventBus
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate

from .deps import get_event_bus, get_pool


def get_execution_service(
    pool: asyncpg.Pool = Depends(get_pool),
    policy: RiskPolicy = Depends(get_risk_policy),
    event_bus: EventBus = Depends(get_event_bus),
) -> ExecutionService:
    # EO-05 — 실행 시작(FD-16.3 start())도 실행 루프(EO-03/EO-04)와 동일한
    # kill switch 게이트를 물려, 운영자가 kill switch를 올렸을 때 이미
    # RUNNING인 실행뿐 아니라 새로 "시작"을 누르는 경로도 막는다.
    return ExecutionService(
        pool,
        policy,
        pre_start_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
        publish=event_bus.publish,
    )


def get_execution_monitoring_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExecutionMonitoringService:
    return ExecutionMonitoringService(pool)
