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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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
    ReconciliationRunAlreadyExists,
    ReconciliationState,
    RunState,
)
from tests.integration.conftest import create_test_tenant

# task-9456 F1 DEEPEN(mandates test_postgres_repository_rls.py와 동일 차용
# 근거)과 같은 예산 — 전용 예산 항목이 없는 단일 실DB 왕복에 "주문 제출->ACK
# p95 50ms(paper)"를 차용한다.
_RLS_SELECT_P95_BUDGET_MS = 50.0


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


class _AppRolePool:
    """실제 `pool`을 감싸, `acquire()`가 내주는 커넥션의 역할만 `aios_app`으로
    낮춘다 — `tests/foundation/integration/mandates/test_postgres_repository_rls.py`
    의 동일 클래스와 같은 이유(`aios_app`은 LOGIN 권한이 없어 별도 자격증명으로
    접속할 수 없다)로, 슈퍼유저 커넥션 안에서 매 acquire마다 `SET ROLE aios_app`을
    걸고 반환 전 `RESET ROLE`로 되돌린다."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._pool.acquire() as conn:
            await conn.execute("SET ROLE aios_app")
            try:
                yield conn
            finally:
                await conn.execute("RESET ROLE")


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


def _state(
    tenant_id, target_ref, status: Classification = Classification.HEALTHY
) -> ReconciliationState:
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

    found = await repo.get_run_by_input_hash(target_ref, "hash-found", tenant_id)

    assert found is not None
    assert found.id == inserted.id
    assert len(found.items) == 1


async def test_get_run_by_input_hash_not_found_returns_none(pool, repo):
    """negative — 존재하지 않는 (target_ref, input_hash) 조합."""
    found = await repo.get_run_by_input_hash(uuid4(), "no-such-hash", uuid4())

    assert found is None


async def test_insert_run_with_items_duplicate_input_hash_returns_existing(pool, repo):
    """실패주입(task-8955) — UNIQUE(target_ref, input_hash) 위반은 raw
    `asyncpg.UniqueViolationError`로 새지 않는다. REC-004 dedupe는 application
    계층의 `get_run_by_input_hash` 사전조회로 흔한 경로만 회피하고, 두
    `reconcile_account` 호출이 동시에 그 사전조회를 통과하는 경쟁(CI에서 실측된
    `test_concurrent_resync_is_serialized_by_position_lock` 실패)은 어댑터가
    `ReconciliationRunAlreadyExists`로 승자의 행을 실어 알려야 한다 — 마이그레이션
    f2b8e5d1a734 docstring이 약속한 "두 번째 삽입 시도는 기존 행을 반환"."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    first = await repo.insert_run_with_items(_run(tenant_id, target_ref, input_hash="dup-hash"), ())

    with pytest.raises(ReconciliationRunAlreadyExists) as exc_info:
        await repo.insert_run_with_items(_run(tenant_id, target_ref, input_hash="dup-hash"), ())

    assert exc_info.value.existing.id == first.id
    assert exc_info.value.existing.input_hash == "dup-hash"


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


# --- F1(task-9456) RLS tenant GUC 바인딩 — `reconciliation_run`은 RLS
# ENABLE+FORCE(b3c7f19ad2e6/c9f4e2a1b6d7)가 걸려 있다. 아래 테스트들은
# `tests/foundation/integration/mandates/test_postgres_repository_rls.py`와
# 동일하게 `test_rls_foundation.py`처럼 AppRoleTx로 SQL을 직접 실행해 우회하지
# 않고, 실제 `PostgresReconciliationRepository` 메서드 호출 경로를 `aios_app`
# 역할 아래에서 그대로 태운다. ----------------------------------------------


async def test_get_run_by_input_hash_returns_row_when_tenant_guc_is_bound(pool, repo):
    """감사 재현 후반부("GUC 주입 후 동일 조회 1행") — 이 리프의 수정
    (`tenant_transaction` 경유)이 `aios_app` 롤 아래에서도 정상 테넌트의
    조회를 1행으로 되돌리는지 확인한다."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    inserted = await repo.insert_run_with_items(
        _run(tenant_id, target_ref, input_hash="app-role-hash"), (_item(),)
    )
    app_role_repo = PostgresReconciliationRepository(_AppRolePool(pool))

    found = await app_role_repo.get_run_by_input_hash(target_ref, "app-role-hash", tenant_id)

    assert found is not None
    assert found.id == inserted.id


async def test_get_run_by_input_hash_fails_closed_when_guc_binding_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pool, repo
):
    """실패주입 + 적색 게이트 재현: F1이 수정 전에 실제로 겪던 증상을 그대로
    재현한다 — `tenant_transaction`이 걸리지 않으면(`SET ROLE aios_app`만 걸린
    평범한 `pool.acquire()`와 동일한 상태), 정상 테넌트가 자기 run을 조회해도
    None이어야 한다(0행/거부). `tenant_transaction`만 GUC를 걸지 않는 가짜로
    바꿔치기해 고쳐진 코드가 이 몽키패치 없이는 통과하지 못했던 상태
    (fail-open이 아니라 fail-closed)를 회귀로 잠근다."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    await repo.insert_run_with_items(
        _run(tenant_id, target_ref, input_hash="unbound-hash"), (_item(),)
    )

    @asynccontextmanager
    async def _unbound_tenant_transaction(
        pool_arg: asyncpg.Pool, _tenant_id: UUID | None
    ) -> AsyncIterator[asyncpg.Connection]:
        async with pool_arg.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.reconciliation.adapters.postgres_repository.tenant_transaction",
        _unbound_tenant_transaction,
    )
    app_role_repo = PostgresReconciliationRepository(_AppRolePool(pool))

    found = await app_role_repo.get_run_by_input_hash(target_ref, "unbound-hash", tenant_id)

    assert found is None


async def test_get_run_by_input_hash_cross_tenant_returns_nothing(pool, repo):
    """negative(교차 테넌트): tenant A의 GUC로 tenant B의 run을 조회해도
    0행이어야 한다 — WHERE의 tenant_id=A와 GUC로 바인딩된 app.tenant_id=A가
    이중으로 B의 행을 걸러낸다."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    target_ref = uuid4()
    await repo.insert_run_with_items(
        _run(tenant_b, target_ref, input_hash="cross-tenant-hash"), (_item(),)
    )
    app_role_repo = PostgresReconciliationRepository(_AppRolePool(pool))

    found = await app_role_repo.get_run_by_input_hash(target_ref, "cross-tenant-hash", tenant_a)

    assert found is None


async def test_insert_run_with_items_fails_closed_without_tenant_guc(
    monkeypatch: pytest.MonkeyPatch, pool
):
    """실패주입(쓰기 경로): `tenant_transaction`이 걸리지 않으면 `aios_app`
    롤 아래의 INSERT는 `reconciliation_run`의 RLS `WITH CHECK`를 통과하지
    못해 예외로 fail-closed 되어야 한다 — 조용히 다른 테넌트 소유로 잘못
    쓰이거나 성공한 척하지 않는다."""

    @asynccontextmanager
    async def _unbound_tenant_transaction(
        pool_arg: asyncpg.Pool, _tenant_id: UUID | None
    ) -> AsyncIterator[asyncpg.Connection]:
        async with pool_arg.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.reconciliation.adapters.postgres_repository.tenant_transaction",
        _unbound_tenant_transaction,
    )
    tenant_id = await _tenant(pool)
    app_role_repo = PostgresReconciliationRepository(_AppRolePool(pool))

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_role_repo.insert_run_with_items(_run(tenant_id, uuid4()), ())


async def _get_run_by_input_hash_p95_ms(
    app_role_repo: PostgresReconciliationRepository,
    target_ref: UUID,
    input_hash: str,
    tenant_id: UUID,
    *,
    n: int,
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await app_role_repo.get_run_by_input_hash(target_ref, input_hash, tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_get_run_by_input_hash_under_app_role_p95_under_borrowed_order_ack_budget(pool, repo):
    """수치 성능 단언: `tenant_transaction` + `SET ROLE aios_app` + RLS 정책
    평가를 포함한 단일 실DB 왕복의 p95가 예산(위 상수) 이내인지 30회 반복으로
    확인한다."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    await repo.insert_run_with_items(
        _run(tenant_id, target_ref, input_hash="perf-hash"), (_item(),)
    )
    app_role_repo = PostgresReconciliationRepository(_AppRolePool(pool))

    p95_ms = await _get_run_by_input_hash_p95_ms(
        app_role_repo, target_ref, "perf-hash", tenant_id, n=30
    )

    assert p95_ms < _RLS_SELECT_P95_BUDGET_MS
