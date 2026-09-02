"""order_service.submit_order()의 pre_submit_gate 배선 — 전수감사 §6 회귀.

foundation_gate.make_foundation_pre_submit_gate()가 실제 risk_gate/mandates
DB를 상대로 두 계층(kill switch 항상, mandate 있으면 정식평가)을 올바르게
적용하는지 확인한다. tests/integration/test_order_service.py의 기존 12개
테스트(pre_submit_gate 미지정)는 이 변경으로 전혀 건드리지 않는다."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.order_service.foundation_gate import (
    REQUIRE_MANDATE_ENV_VAR,
    make_foundation_pre_submit_gate,
)
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


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


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"gate-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _market_order(execution_id: int) -> Order:
    return Order(
        client_order_id=f"gate-test-{uuid.uuid4().hex}",
        strategy_id="strat-1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )


async def test_active_kill_switch_denies_unmandated_legacy_submit(pool, risk_repo):
    """1층 — mandate가 아예 없어도 kill switch는 legacy 주문을 막는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="테스트 — legacy 경로 킬스위치",
    )
    gate = make_foundation_pre_submit_gate(pool)

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )


async def test_unmandated_submit_passes_with_audit_event(pool, risk_repo):
    """2층 — mandate_revision_id가 없으면(기존 실행 전부) DENY가 아니라
    audit_log만 남기고 통과한다(RSK-002 소급적용으로 인한 회귀 방지)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool)

    result = await submit_order(
        _market_order(execution_id),
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=gate,
    )
    assert result.exchange_order_id is not None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(execution_id),
        )
    assert row is not None


async def test_require_mandate_flag_denies_unmandated_submit(pool, risk_repo, monkeypatch):
    """AIOS_REQUIRE_MANDATE_FOR_SUBMIT=1이면 mandate 없는 실행도 DENY —
    RSK-002를 최종적으로 만족시키는 스위치."""
    monkeypatch.setenv(REQUIRE_MANDATE_ENV_VAR, "1")
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )
    assert "RISK_MANDATE_REQUIRED" in exc_info.value.reason_codes


async def test_mandated_submit_denied_when_policy_violated(
    pool, risk_repo, mandate_repo, trust_repo
):
    """mandate가 연결된 실행은 정식 정책평가를 거친다 — 정책 위반이면
    DENY(예: 활성 mandate가 PAUSED 상태)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    await pause_mandate(mandate_repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool)
    mandate = await mandate_repo.get_mandate(user_id)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
            mandate_revision_id=mandate.active_revision_id,
        )
    assert "STATE_MANDATE_PAUSED" in exc_info.value.reason_codes


async def test_mandated_submit_allowed_when_policy_clean(pool, risk_repo, mandate_repo, trust_repo):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    gate = make_foundation_pre_submit_gate(pool)
    result = await submit_order(
        _market_order(execution_id),
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=gate,
        mandate_revision_id=mandate.active_revision_id,
    )
    assert result.exchange_order_id is not None
