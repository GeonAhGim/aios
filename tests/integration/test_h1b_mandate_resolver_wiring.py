"""H-1b(task-3369, ADR-2026-09-09-B) — 프로덕션 조립부의 require_mandate=True
전환 + H-1a resolver(`resolve_binding.resolve_mandate_revision`) 배선.

`execution_control.start()`(`src/services/execution_control.py:69-75`)는
`OrderContext(mandate_revision_id=None)`을 항상 만든다 — 이 컬럼을 채우는
UI 경로가 아직 없기 때문(module docstring 참고, task-1806). H-1b 이전에는
그래서 `require_mandate=True`로 그냥 전환하면 mandate가 실제로 있는 tenant도
전부 REJECT됐을 것이다 — 이 파일은 그 회귀를 막는 resolver 배선이 실제
프로덕션 조립부(`src/api/execution_deps.py::get_execution_service`)를 통해
동작하는지 확인한다(직접 `make_foundation_pre_submit_gate`를 호출하는
`test_execution_service_risk_gate.py`/`test_order_service_risk_gate.py`와
달리, 이 파일은 조립 함수 자체를 부른다)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.api.execution_deps import get_execution_service
from src.core.loader.risk_policy_loader import load_risk_policy
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.execution_service import ExecutionControlError
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import NoopEventBus, create_test_tenant


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
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


@pytest.fixture
def production_service(pool):
    """`src/api/execution_deps.py::get_execution_service`와 동일 조립 —
    FastAPI `Depends`는 직접 호출 시 그냥 파이썬 함수라 인자를 그대로 넘긴다."""
    return get_execution_service(pool=pool, policy=load_risk_policy(), event_bus=NoopEventBus())


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"h1b-test-{uuid4().hex[:8]}"
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


async def test_unmandated_start_now_rejected_via_production_wiring(production_service, pool):
    """require_mandate=True로 바뀐 실제 프로덕션 조립(`get_execution_service`)이
    — mandate가 전혀 없는 tenant의 실행 시작을 REJECT한다(H-1 DoD "무 mandate
    주문 = REJECT"). 예전 동작(감사로그만 남기고 통과)은 더 이상 없다."""
    user_id = await create_test_tenant(pool)
    created = await _create_execution(production_service, pool, user_id)

    with pytest.raises(ExecutionControlError, match="RISK_MANDATE_REQUIRED"):
        await production_service.start(created.id, user_id)

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_executions WHERE id = $1", created.id
        )
        audit_row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(created.id),
        )
    assert status != "RUNNING"
    assert audit_row is not None  # REJECT 이전에도 감사 기록은 남는다


async def test_mandated_tenant_start_still_allowed_via_resolver(
    production_service, pool, mandate_repo, trust_repo
):
    """회귀 방지(paper 흐름) — `execution_control.start()`는 여전히
    `mandate_revision_id=None`인 `OrderContext`만 만든다(UI가 채우지 않음),
    그런데도 tenant에 ACTIVE mandate가 있으면 H-1a resolver
    (`resolve_binding.resolve_mandate_revision`)가 그 자리를 채워 정상
    ALLOW로 진행한다 — require_mandate=True 전환이 기존 mandate 연결
    tenant의 정상 PAPER 흐름까지 깨뜨리지 않았음을 증명한다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    created = await _create_execution(production_service, pool, user_id)

    result = await production_service.start(created.id, user_id)

    assert result.status == "RUNNING"
