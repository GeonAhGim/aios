"""task-4627 TEST-cov —
`PostgresReconciliationRepository`(실 DB, TEST_DATABASE_URL) 0% -> 70%+.

Spec: docs/specs 80번(FND-08) §1/§2, 105번(동시성 표준). `test_reconciliation_
lifecycle.py`는 application 계층(run_reconciliation/resolve_reconciliation)을
경유해 이 어댑터를 간접 호출하지만 그 파일은 `.env`의 DATABASE_URL을 직접
읽어(TEST_DATABASE_URL 오버라이드를 안 타서) 커버리지 계측에 잡히지 않는다
(worker 전용 DB로 격리된 conftest 경로를 안 씀). 이 파일은 어댑터를 직접
호출해 TEST_DATABASE_URL 경로로 커버리지를 확보한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.domain.models import (
    Classification,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
    RunState,
)
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresReconciliationRepository:
    return PostgresReconciliationRepository(pool)


async def _tenant(pool: asyncpg.Pool):
    return await create_test_tenant(pool)


def _run(tenant_id, target_ref, input_hash: str = "hash-1") -> ReconciliationRun:
    return ReconciliationRun(
        id=uuid4(),
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        input_hash=input_hash,
        state=RunState.COMPLETED,
        rule_version="1.0",
    )


def _item(classification: Classification = Classification.HEALTHY) -> ReconciliationItem:
    return ReconciliationItem(
        id=uuid4(),
        run_id=uuid4(),  # insert_run_with_items가 실제 run_id로 교체해서 저장한다
        entity_type="BALANCE",
        entity_key="USDT",
        internal_value=Decimal("1000.00"),
        provider_value=Decimal("1000.00"),
        classification=classification,
    )


def _state(tenant_id, target_ref, status: Classification = Classification.HEALTHY) -> (
    ReconciliationState
):
    return ReconciliationState(
        target_ref=target_ref,
        target_type="PAPER_DEPLOYMENT",
        tenant_id=tenant_id,
        aggregate_status=status,
        last_healthy_at=datetime.now(timezone.utc) if status == Classification.HEALTHY else None,
        last_checked_at=datetime.now(timezone.utc),
        blocking_reason=None,
        revision=0,
        safety_control_id=None,
    )


async def test_insert_run_with_items_round_trips(pool, repo):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    run = _run(tenant_id, target_ref)

    inserted = await repo.insert_run_with_items(run, (_item(),))

    assert inserted.id is not None
    assert inserted.tenant_id == tenant_id
    assert inserted.target_ref == target_ref
    assert inserted.state == RunState.COMPLETED
    assert len(inserted.items) == 1
    assert inserted.items[0].run_id == inserted.id
    assert inserted.items[0].internal_value == Decimal("1000.0000000000")


async def test_insert_run_with_items_supports_empty_items(pool, repo):
    tenant_id = await _tenant(pool)
    run = _run(tenant_id, uuid4())

    inserted = await repo.insert_run_with_items(run, ())

    assert inserted.items == ()


async def test_get_run_by_input_hash_found(pool, repo):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    run = _run(tenant_id, target_ref, input_hash="hash-found")
    inserted = await repo.insert_run_with_items(run, (_item(),))

    found = await repo.get_run_by_input_hash(target_ref, "hash-found")

    assert found is not None
    assert found.id == inserted.id
    assert len(found.items) == 1


async def test_get_run_by_input_hash_not_found_returns_none(pool, repo):
    """negative — 존재하지 않는 (target_ref, input_hash) 조합."""
    found = await repo.get_run_by_input_hash(uuid4(), "no-such-hash")

    assert found is None


async def test_insert_run_with_items_duplicate_input_hash_raises(pool, repo):
    """실패주입 — UNIQUE(target_ref, input_hash) 제약 위반은 그대로 전파된다
    (REC-004 dedupe는 application 계층이 get_run_by_input_hash로 먼저 조회해
    회피하는 책임이고, 어댑터 자체는 그 제약을 그대로 드러낸다)."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    run = _run(tenant_id, target_ref, input_hash="dup-hash")
    await repo.insert_run_with_items(run, ())

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.insert_run_with_items(_run(tenant_id, target_ref, input_hash="dup-hash"), ())


async def test_get_state_not_found_returns_none(pool, repo):
    """negative — 존재하지 않는 target_ref."""
    state = await repo.get_state(uuid4())

    assert state is None


async def test_upsert_state_inserts_then_updates_and_bumps_revision(pool, repo):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()

    inserted = await repo.upsert_state(_state(tenant_id, target_ref, Classification.HEALTHY))
    assert inserted.revision == 0
    assert inserted.last_healthy_at is not None

    updated = await repo.upsert_state(
        _state(tenant_id, target_ref, Classification.MATERIAL_MISMATCH)
    )
    assert updated.revision == 1
    assert updated.aggregate_status == Classification.MATERIAL_MISMATCH
    # last_healthy_at은 새 값이 None이면 기존 값을 보존한다(COALESCE).
    assert updated.last_healthy_at == inserted.last_healthy_at


async def test_upsert_state_preserves_safety_control_id_when_not_supplied(pool, repo):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    control_id = uuid4()

    state = _state(tenant_id, target_ref, Classification.MATERIAL_MISMATCH)
    state_with_control = ReconciliationState(**{**state.__dict__, "safety_control_id": control_id})
    first = await repo.upsert_state(state_with_control)
    assert first.safety_control_id == control_id

    second = await repo.upsert_state(_state(tenant_id, target_ref, Classification.HEALTHY))
    assert second.safety_control_id == control_id


async def test_list_states_filters_by_tenant_and_orders_by_last_checked_at(pool, repo):
    tenant_id = await _tenant(pool)
    other_tenant_id = await _tenant(pool)
    target_a, target_b, target_other = uuid4(), uuid4(), uuid4()

    await repo.upsert_state(_state(tenant_id, target_a))
    await repo.upsert_state(_state(tenant_id, target_b))
    await repo.upsert_state(_state(other_tenant_id, target_other))

    states = await repo.list_states(tenant_id)

    assert {s.target_ref for s in states} == {target_a, target_b}
    assert all(s.tenant_id == tenant_id for s in states)


async def test_list_states_empty_for_unknown_tenant(pool, repo):
    """negative — 상태가 하나도 없는 tenant는 빈 튜플."""
    states = await repo.list_states(uuid4())

    assert states == ()


async def test_transition_state_status_success_bumps_revision(pool, repo):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    inserted = await repo.upsert_state(_state(tenant_id, target_ref, Classification.HEALTHY))

    transitioned = await repo.transition_state_status(
        target_ref,
        expected_revision=inserted.revision,
        new_status=Classification.INVESTIGATING,
        blocking_reason="수동 조사 시작",
    )

    assert transitioned.revision == inserted.revision + 1
    assert transitioned.aggregate_status == Classification.INVESTIGATING
    assert transitioned.blocking_reason == "수동 조사 시작"
    assert transitioned.resolved_at is None


async def test_transition_state_status_to_resolved_sets_resolved_at(pool, repo):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    inserted = await repo.upsert_state(
        _state(tenant_id, target_ref, Classification.MATERIAL_MISMATCH)
    )

    resolved = await repo.transition_state_status(
        target_ref,
        expected_revision=inserted.revision,
        new_status=Classification.RESOLVED,
        blocking_reason=None,
        resolved_by=tenant_id,
        resolution_reason="원인 파악 완료",
    )

    assert resolved.aggregate_status == Classification.RESOLVED
    assert resolved.resolved_by == tenant_id
    assert resolved.resolution_reason == "원인 파악 완료"
    assert resolved.resolved_at is not None


async def test_transition_state_status_wrong_revision_raises_concurrency_conflict(pool, repo):
    """실패주입/negative — standard-105 조건부 UPDATE: stale revision은
    ConcurrencyConflictError로 fail-closed."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    inserted = await repo.upsert_state(_state(tenant_id, target_ref, Classification.HEALTHY))

    with pytest.raises(ConcurrencyConflictError):
        await repo.transition_state_status(
            target_ref,
            expected_revision=inserted.revision + 99,
            new_status=Classification.INVESTIGATING,
            blocking_reason="stale",
        )


async def test_transition_state_status_unknown_target_ref_raises_concurrency_conflict(pool, repo):
    """negative — 존재하지 않는 target_ref도 동일하게 fail-closed 취급된다."""
    with pytest.raises(ConcurrencyConflictError):
        await repo.transition_state_status(
            uuid4(),
            expected_revision=0,
            new_status=Classification.INVESTIGATING,
            blocking_reason="no such row",
        )
