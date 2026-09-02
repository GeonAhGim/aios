"""Risk & Safety Gate adversarial 테스트 — 다른 tenant의 안전 통제를
읽거나 해제할 수 없어야 한다(73번 TRU-006과 동일 원칙), 그리고 다른
tenant의 connection을 게이트 평가에 끼워 넣을 수 없어야 한다."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

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
from src.foundation.risk_gate.application.evaluate_risk_gate import (
    CrossTenantConnectionReferenceError,
    evaluate_risk_gate,
)
from src.foundation.risk_gate.domain.models import GateKind, SafetyScope
from src.foundation.risk_gate.projections import build_safety_control_list_view
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def connection_repo(pool):
    return PostgresConnectionRepository(pool)


async def test_cannot_deactivate_another_accounts_control(pool, repo):
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=owner_id,
        actor_subject_id=owner_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(owner_id),
        reason="owner's control",
    )

    with pytest.raises(UnauthorizedSafetyControlScopeError):
        await deactivate_safety_control(
            repo, tenant_id=attacker_id, actor_is_admin=False, control_id=control.id
        )

    still_active = await repo.get_safety_control(control.id)
    assert still_active.state.value == "ACTIVE"


async def test_safety_control_list_view_never_includes_another_tenants_control(pool, repo):
    """GLOBAL-scope control은 정의상 모든 tenant에게 보여야 하므로(48번 §4),
    이 테스트는 그 대신 tenant_a에 귀속된 ACCOUNT-scope control이 tenant_b
    쪽 목록에 섞이지 않는지만 확인한다 — 다른 테스트가 이미 GLOBAL control을
    걸어뒀을 수 있는 공유 DB 환경이라 목록 자체가 비어있길 기대하지 않는다."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=tenant_a,
        actor_subject_id=tenant_a,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_a),
        reason="tenant A only",
    )

    view_b = await build_safety_control_list_view(repo, tenant_b)

    assert control.id not in [c.id for c in view_b.controls]
    assert str(tenant_a) not in [c.scope_ref for c in view_b.controls]


async def test_cannot_reference_another_tenants_connection_in_evaluation(
    pool, repo, mandate_repo, connection_repo
):
    tenant_id = await create_test_user(pool)
    with pytest.raises(CrossTenantConnectionReferenceError):
        await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.DEPLOYMENT,
            connection_id=uuid4(),
        )
