"""ExecutionService.start()의 pre_start_gate 배선 — 전수감사 §6 회귀.

order_service.foundation_gate.make_foundation_pre_submit_gate()를 그대로
재사용한다(타입이 동일해 새 foundation 코드를 만들지 않음) — 여기서는
그 재사용이 실제로 동작하는지만 확인한다. EO-05 — `pre_start_gate`는
더 이상 Optional이 아니다(I-01, 생성 자체가 게이트 없이는 불가능). 이전
버전의 이 파일에 있던 `test_gate_none_by_default_matches_existing_behavior`
(게이트 미지정 시 기본 통과를 검증하던 테스트)는 그 자체가 지금은 존재할
수 없는 상태라 제거했다 — `tests/integration/test_execution_control.py`를
포함한 다른 모든 ExecutionService 생성부도 이 리프에서 실제 게이트를
주입하도록 함께 바뀌었다."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.execution_service import ExecutionControlError, ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome, OrderContext
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def risk_repo(pool):
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


@pytest.fixture
def gated_service(pool):
    return ExecutionService(
        pool, load_risk_policy(), pre_start_gate=make_foundation_pre_submit_gate(pool)
    )


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"gate-test-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _link_credential(pool, user_id, exchange="bitget"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, $2, $3, $3)",
            user_id,
            exchange,
            b"dummy",
        )


async def _create_execution(service, pool, user_id):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    return await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )


async def test_active_kill_switch_denies_unmandated_start(gated_service, pool, risk_repo):
    """1층 — mandate가 없어도 kill switch는 legacy 실행 시작을 막는다."""
    user_id = await create_test_user(pool)
    created = await _create_execution(gated_service, pool, user_id)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="테스트 — 실행 시작 킬스위치",
    )

    with pytest.raises(ExecutionControlError):
        await gated_service.start(created.id, user_id)

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_executions WHERE id = $1", created.id
        )
    assert status != "RUNNING"


async def test_unmandated_start_passes_with_audit_event(gated_service, pool):
    """2층 — mandate_revision_id가 없으면(기존 실행 전부) DENY 대신
    audit_log만 남기고 통과한다."""
    user_id = await create_test_user(pool)
    created = await _create_execution(gated_service, pool, user_id)

    result = await gated_service.start(created.id, user_id)
    assert result.status == "RUNNING"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(created.id),
        )
    assert row is not None


async def test_mandate_linked_start_denied_when_mandate_paused(
    pool, risk_repo, mandate_repo, trust_repo
):
    """mandate_revision_id가 연결된 실행은(컬럼이 생기기 전까지는 이
    테스트가 직접 게이트를 만들어 검증) 정식 정책평가를 거친다."""
    user_id = await create_test_user(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    await pause_mandate(mandate_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    gate = make_foundation_pre_submit_gate(pool)
    decision = await gate(
        OrderContext(
            user_id=user_id,
            execution_id=1,
            exchange="bitget",
            mandate_revision_id=mandate.active_revision_id,
        )
    )
    assert decision.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in decision.reason_codes
