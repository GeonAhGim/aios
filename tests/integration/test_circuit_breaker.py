"""9.4/9.4b 통합테스트 — 실제 dev DB의 system_safety_state(단일 행) 대상.

각 테스트 시작 전 normal/재가동없음으로 리셋해 전역 싱글톤 행 상태를
격리한다.
"""
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    CircuitBreakerService,
    compute_level,
)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def policy():
    return load_risk_policy().circuit_breaker


def test_compute_level_picks_most_severe_indicator(policy):
    metrics = CircuitBreakerMetrics(
        api_error_rate_pct=Decimal("12"),  # warning 임계(10) 초과 -> warning
        data_delay_sec=Decimal("6"),  # halted 임계(5) 초과 -> halted
    )
    assert compute_level(metrics, policy) == CircuitBreakerLevel.HALTED


def test_compute_level_normal_when_nothing_triggered(policy):
    assert compute_level(CircuitBreakerMetrics(), policy) == CircuitBreakerLevel.NORMAL


async def test_warning_auto_upgrades_and_downgrades(pool, policy):
    service = CircuitBreakerService(pool, policy)

    up = await service.evaluate(CircuitBreakerMetrics(api_error_rate_pct=Decimal("12")))
    assert up.level == CircuitBreakerLevel.WARNING

    down = await service.evaluate(CircuitBreakerMetrics())
    assert down.level == CircuitBreakerLevel.NORMAL  # warning은 자동 하향 허용


async def test_halted_does_not_auto_downgrade_and_creates_reactivation_request(pool, policy):
    service = CircuitBreakerService(pool, policy)

    halted = await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))
    assert halted.level == CircuitBreakerLevel.HALTED

    recovered_metrics = CircuitBreakerMetrics()
    result = await service.evaluate(recovered_metrics)

    assert result.level == CircuitBreakerLevel.HALTED  # 자동 하향 안 됨
    assert result.reactivation_approval_id is not None

    request = await approval.get_request(pool, result.reactivation_approval_id)
    assert request.scope == "PLATFORM"
    assert request.mandatory_wait_seconds == 180


async def test_reactivation_approval_completes_transition_to_normal(pool, policy):
    service = CircuitBreakerService(pool, policy)
    await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))
    state = await service.evaluate(CircuitBreakerMetrics())
    request_id = state.reactivation_approval_id

    # 실제 180초 대기 없이 승인 가능 상태로 만든다(대기시간 경과 시뮬레이션).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = now() - interval '181 seconds' "
            "WHERE id = $1",
            request_id,
        )
    from uuid import uuid4

    await approval.approve(pool, request_id, uuid4())

    final_state = await service.check_reactivation()
    assert final_state.level == CircuitBreakerLevel.NORMAL
    assert final_state.reactivation_approval_id is None


async def test_worsening_during_reactivation_wait_cancels_request(pool, policy):
    service = CircuitBreakerService(pool, policy)
    await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))
    pending = await service.evaluate(CircuitBreakerMetrics())
    request_id = pending.reactivation_approval_id
    assert request_id is not None

    # 대기 중 재악화 — 다시 halted를 유발하는 지표
    worsened = await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))

    assert worsened.level == CircuitBreakerLevel.HALTED
    assert worsened.reactivation_approval_id is None
    cancelled_request = await approval.get_request(pool, request_id)
    assert cancelled_request.status == "CANCELLED"


async def test_escalation_from_halted_to_emergency_allowed_even_while_pending(pool, policy):
    service = CircuitBreakerService(pool, policy)
    await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))  # halted

    emergency = await service.evaluate(
        CircuitBreakerMetrics(daily_loss_pct=Decimal("6"))  # emergency 임계(5) 초과
    )
    assert emergency.level == CircuitBreakerLevel.EMERGENCY
