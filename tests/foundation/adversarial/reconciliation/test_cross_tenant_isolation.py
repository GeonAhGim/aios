"""Reconciliation & Resilience adversarial 테스트 — REC-008 "tenant/operator
isolation ... hold": 다른 tenant의 reconciliation state를 조회/resolve할 수
없어야 한다."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    resolve_reconciliation,
)
from src.foundation.reconciliation.domain.models import Classification as DomainClassification
from src.foundation.reconciliation.domain.models import (
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
    RunState,
)
from src.foundation.reconciliation.projections import build_reconciliation_state_list_view
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
    return PostgresReconciliationRepository(pool)


async def _seed_material_mismatch(repo, tenant_id):
    now = datetime.now(timezone.utc)
    run = ReconciliationRun(
        id=uuid4(),
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=tenant_id,
        connection_id=None,
        input_hash="adversarial-fixed-hash",
        state=RunState.COMPLETED,
        rule_version="v1",
    )
    item = ReconciliationItem(
        id=uuid4(),
        run_id=uuid4(),
        entity_type="BALANCE",
        entity_key="USDT",
        internal_value=Decimal("100"),
        provider_value=Decimal("1"),
        classification=DomainClassification.MATERIAL_MISMATCH,
    )
    await repo.insert_run_with_items(run, (item,))
    await repo.upsert_state(
        ReconciliationState(
            target_ref=tenant_id,
            target_type="PAPER_DEPLOYMENT",
            tenant_id=tenant_id,
            aggregate_status=DomainClassification.MATERIAL_MISMATCH,
            last_healthy_at=None,
            last_checked_at=now,
            blocking_reason="INTEGRITY_RECONCILIATION_MISMATCH:MATERIAL_MISMATCH",
            revision=0,
            safety_control_id=None,
        )
    )


async def test_cannot_resolve_another_tenants_reconciliation_state(pool, repo):
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    await _seed_material_mismatch(repo, owner_id)

    with pytest.raises(CrossTenantReconciliationAccessError):
        await resolve_reconciliation(
            repo,
            tenant_id=attacker_id,
            actor_subject_id=attacker_id,
            target_ref=owner_id,
            reason="공격 시도",
        )

    state = await repo.get_state(owner_id)
    assert state.aggregate_status.value == "MATERIAL_MISMATCH"


async def test_state_list_view_never_includes_another_tenants_state(pool, repo):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await _seed_material_mismatch(repo, tenant_a)

    view_b = await build_reconciliation_state_list_view(repo, tenant_b)

    assert tenant_a not in [s.target_ref for s in view_b.states]
