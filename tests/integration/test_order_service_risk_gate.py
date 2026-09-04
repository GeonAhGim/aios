"""order_service.submit_order()의 pre_submit_gate 배선 — 전수감사 §6 회귀 +
R-36(mandate 필수 기본값·fence_snapshot 관통).

foundation_gate.make_foundation_pre_submit_gate()가 실제 risk_gate/mandates
DB를 상대로 fence 관통(R-33/R-35 위임)·mandate 필수(I-01 fail-closed)를
올바르게 적용하는지 확인한다. tests/integration/test_order_service.py의
기존 12개 테스트(pre_submit_gate 미지정)는 이 변경으로 전혀 건드리지
않는다(그 경로는 게이트 자체를 안 거친다)."""
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
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import OrderContext
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
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )
    assert any(code.startswith("RISK_KILL_SWITCH_ACTIVE_") for code in exc_info.value.reason_codes)


async def test_unmandated_submit_denied(pool, risk_repo):
    """R-36 — mandate_revision_id가 없으면(기존 실행 전부) 더 이상 통과하지
    않는다(I-01 fail-closed). env var 우회 경로는 제거됐다 — 이 결과는
    조건 없이 항상 적용된다. 거부되기 전에도 감사 기록은 남긴다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )
    assert exc_info.value.reason_codes == ("RISK_MANDATE_REQUIRED",)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(execution_id),
        )
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert row is not None
    assert order_count == 0


async def test_mandated_submit_denied_when_policy_violated(
    pool, risk_repo, mandate_repo, trust_repo
):
    """mandate가 연결된 실행은 정식 정책평가를 거친다 — 정책 위반이면
    DENY(예: 활성 mandate가 PAUSED 상태)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    await pause_mandate(mandate_repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
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

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    result = await submit_order(
        _market_order(execution_id),
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=gate,
        mandate_revision_id=mandate.active_revision_id,
    )
    assert result.exchange_order_id is not None


async def test_gate_decision_carries_fence_snapshot(pool, risk_repo, mandate_repo, trust_repo):
    """R-33 fence 관통 — ALLOW 결정에도 그 판단의 근거인 F0가 그대로
    실린다(R-37 fenced_submit이 다음 단계에서 재사용할 값)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    decision = await gate(
        OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=mandate.active_revision_id,
        )
    )
    assert decision.fence_snapshot
    assert any(key.startswith("GLOBAL:") for key in decision.fence_snapshot)


async def test_stale_fence_denies_even_when_no_control_is_currently_active(
    pool, risk_repo, mandate_repo, trust_repo
):
    """R-36 negative — 관측(F0) 이후 어떤 scope든 fence token이 증가했다면,
    지금 이 순간 활성 control이 하나도 없어도(비활성화까지 됐어도) DENY —
    단조증가 토큰이라 "이미 무언가 발동한 적 있음" 자체를 stale로 본다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    context = OrderContext(
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=mandate.active_revision_id,
    )
    observed = await gate(context)
    assert observed.outcome.value == "ALLOW"

    control = await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="테스트 — fence stale 유발 후 즉시 해제",
    )
    await deactivate_safety_control(
        risk_repo, tenant_id=user_id, actor_is_admin=True, control_id=control.id
    )

    stale_decision = await gate(
        OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=mandate.active_revision_id,
            observed_fence=observed.fence_snapshot,
        )
    )
    assert stale_decision.outcome.value == "DENY"
    assert stale_decision.reason_codes == ("RISK_FENCE_STALE",)
