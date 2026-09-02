"""FND-06 Risk & Safety Gate 통합테스트 — 실제 dev DB 대상. 48번 §5/78번 §6 중
FND-07(paper_control)/order adapter 없이 재현 가능한 범위(RSK-001~005)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import (
    UnauthorizedSafetyControlScopeError,
    activate_safety_control,
)
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.domain.models import GateKind, SafetyScope
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


@pytest.fixture
def connection_repo(pool):
    return PostgresConnectionRepository(pool)


async def _tenant(pool):
    return await create_test_user(pool)


async def test_evaluate_denies_when_no_active_mandate(pool, repo, mandate_repo, connection_repo):
    """RSK-002 — missing input yields DENY, never implicit ALLOW."""
    tenant_id = await _tenant(pool)
    result = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert result.outcome.value == "DENY"
    assert "RISK_INPUT_MANDATE_MISSING" in result.reason_codes


async def test_evaluate_allows_with_active_mandate_and_no_controls(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """RSK-001 — pinned input/rule produces stable decision."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    first = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert first.outcome.value == "ALLOW"

    # 짧은 TTL 캐시 안에서 같은 입력이면 같은 evaluation을 재사용한다.
    second = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert second.id == first.id


async def test_active_kill_switch_denies_even_with_healthy_mandate(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """RSK-003/48번 §5 acceptance test 1 — 어느 게이트든 kill switch가 최종
    거부권을 갖는다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="사용자 자진 정지 테스트",
    )

    result = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert result.outcome.value == "DENY"
    assert "RISK_KILL_SWITCH_ACTIVE_ACCOUNT" in result.reason_codes


async def test_global_kill_switch_denies_every_tenant(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """48번 §5 acceptance test 4 — global kill switch blocks every tenant's
    new orders."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_a)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_b)

    admin_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=admin_id,
        actor_subject_id=admin_id,
        actor_is_admin=True,
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason="글로벌 정지 테스트",
    )
    try:
        for tenant_id in (tenant_a, tenant_b):
            result = await evaluate_risk_gate(
                repo,
                mandate_repo,
                connection_repo,
                tenant_id=tenant_id,
                gate_kind=GateKind.DEPLOYMENT,
            )
            assert result.outcome.value == "DENY"
            assert "RISK_KILL_SWITCH_ACTIVE_GLOBAL" in result.reason_codes
    finally:
        # GLOBAL 통제는 실제로 전역이라, 안 끄고 두면 이 공유 테스트 DB의
        # 다른 모든 이후 테스트(다른 파일 포함)까지 항상 DENY로 오염시킨다.
        await deactivate_safety_control(
            repo, tenant_id=admin_id, actor_is_admin=True, control_id=control.id
        )


async def test_self_service_cannot_activate_tenant_wide_control(pool, repo):
    tenant_id = await _tenant(pool)
    with pytest.raises(UnauthorizedSafetyControlScopeError):
        await activate_safety_control(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            scope=SafetyScope.TENANT,
            scope_ref=str(tenant_id),
            reason="권한 없는 시도",
        )


async def test_self_service_cannot_activate_another_accounts_control(pool, repo):
    tenant_id = await _tenant(pool)
    other_id = await _tenant(pool)
    with pytest.raises(UnauthorizedSafetyControlScopeError):
        await activate_safety_control(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(other_id),
            reason="다른 계좌를 지정하려는 시도",
        )


async def test_deactivate_marks_inactive_and_is_idempotent_failure(pool, repo):
    tenant_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="해제 테스트",
    )
    deactivated = await deactivate_safety_control(
        repo, tenant_id=tenant_id, actor_is_admin=False, control_id=control.id
    )
    assert deactivated.state.value == "INACTIVE"

    with pytest.raises(ConcurrencyConflictError):
        await deactivate_safety_control(
            repo, tenant_id=tenant_id, actor_is_admin=False, control_id=control.id
        )


async def test_concurrent_activations_never_lose_a_fence_token(pool, repo):
    """RSK-005 — activate control races with submit and fence prevents
    post-control side effect. 여기서는 그 전제조건(fence 증가 자체가
    동시 요청에서도 유실 없이 유일해야 한다)을 105번 §4 형태 A로
    검증한다 — 실제 pre-submit 게이트 배선은 FND-07 이후."""
    tenant_id = await _tenant(pool)

    async def _activate():
        return await activate_safety_control(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(tenant_id),
            reason="동시성 테스트",
        )

    results = await asyncio.gather(*[_activate() for _ in range(5)])
    tokens = sorted(r.fence_token for r in results)
    assert tokens == sorted(set(tokens)), "fence token이 중복됐다 — 유실된 증가가 있다"
    assert len(tokens) == 5
