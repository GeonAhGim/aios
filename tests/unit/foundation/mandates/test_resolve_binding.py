"""H-1a `resolve_mandate_revision()` — 실제 TEST_DATABASE_URL 대상.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-1
(task-3368). DoD: 활성/없음/정지/만료 4종 + `_status_for_state`의 EXPIRED
버킷이 실제로 총 함수인지(negative), 커넥션 실패를 삼키지 않는지(실패 주입),
호출 1회 지연(성능 단언)을 더한다.
"""
from __future__ import annotations

import os
import time
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.resolve_binding import (
    MandateBindingStatus,
    _status_for_state,
    resolve_mandate_revision,
)
from src.foundation.mandates.domain.models import Autonomy, MandateRevision, MandateRevisionState
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMandateRepository(pool)


async def _cleanup(pool: asyncpg.Pool, mandate_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE portfolio_mandate SET active_revision_id = NULL WHERE id = $1", mandate_id
        )
        await conn.execute("DELETE FROM mandate_revision WHERE mandate_id = $1", mandate_id)
        await conn.execute("DELETE FROM portfolio_mandate WHERE id = $1", mandate_id)


def _draft_revision(mandate_id: UUID) -> MandateRevision:
    return MandateRevision(
        id=uuid4(),
        mandate_id=mandate_id,
        revision_no=1,
        state=MandateRevisionState.DRAFT,
        max_total_exposure_pct=80.0,
        max_single_instrument_pct=20.0,
        min_cash_buffer_pct=5.0,
        max_daily_loss_pct=3.0,
        allowed_autonomy=Autonomy.PAPER,
    )


async def test_resolves_active_revision(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)
    portfolio_id = uuid4()
    mandate = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_id)
    try:
        draft = await repo.insert_draft_revision(
            mandate_id=mandate.id, revision_no=1, rules=_draft_revision(mandate.id)
        )
        await repo.activate_revision(mandate.id, draft.id, expected_active_revision_id=None)

        ref = await resolve_mandate_revision(pool, portfolio_id)

        assert ref is not None
        assert ref.status == MandateBindingStatus.ACTIVE
        assert ref.revision.id == draft.id
    finally:
        await _cleanup(pool, mandate.id)


async def test_no_portfolio_mandate_row_returns_none(pool) -> None:
    ref = await resolve_mandate_revision(pool, uuid4())
    assert ref is None


async def test_mandate_without_activated_revision_returns_none(pool, repo) -> None:
    """DRAFT만 있고 한 번도 activate된 적 없는 mandate -- `active_revision_id`가
    NULL 그대로라, JOIN이 행을 하나도 돌려주지 않는 경로(행 자체가 없는 경우와
    같은 코드 경로)를 별도로 거친다."""
    tenant_id = await create_test_tenant(pool)
    portfolio_id = uuid4()
    mandate = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_id)
    try:
        await repo.insert_draft_revision(
            mandate_id=mandate.id, revision_no=1, rules=_draft_revision(mandate.id)
        )

        ref = await resolve_mandate_revision(pool, portfolio_id)

        assert ref is None
    finally:
        await _cleanup(pool, mandate.id)


async def test_resolves_paused_revision(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)
    portfolio_id = uuid4()
    mandate = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_id)
    try:
        draft = await repo.insert_draft_revision(
            mandate_id=mandate.id, revision_no=1, rules=_draft_revision(mandate.id)
        )
        active = await repo.activate_revision(
            mandate.id, draft.id, expected_active_revision_id=None
        )
        await repo.transition_revision_state(
            active.id,
            expected_state=MandateRevisionState.ACTIVE.value,
            new_state=MandateRevisionState.PAUSED.value,
        )

        ref = await resolve_mandate_revision(pool, portfolio_id)

        assert ref is not None
        assert ref.status == MandateBindingStatus.PAUSED
        assert ref.revision.id == draft.id
    finally:
        await _cleanup(pool, mandate.id)


async def test_resolves_expired_when_pointer_targets_superseded_revision(pool, repo) -> None:
    """정상 흐름에서는 `activate_revision`이 이전 ACTIVE를 SUPERSEDED로 바꾸는
    동시에 `active_revision_id`를 새 revision으로 옮기므로(postgres_repository.py
    `activate_revision`), 포인터가 SUPERSEDED를 직접 가리키는 상태는 정상적으로는
    나타나지 않는다. 그래도 resolver는 fail-closed로 EXPIRED를 반환해야 한다
    (evaluate_policy.py의 "ACTIVE/PAUSED가 아니면 거부"와 같은 원칙) -- 방어적
    분기라 raw SQL로 그 상태를 직접 재현한다."""
    tenant_id = await create_test_tenant(pool)
    portfolio_id = uuid4()
    mandate = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_id)
    try:
        draft = await repo.insert_draft_revision(
            mandate_id=mandate.id, revision_no=1, rules=_draft_revision(mandate.id)
        )
        active = await repo.activate_revision(
            mandate.id, draft.id, expected_active_revision_id=None
        )
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE mandate_revision SET state = 'SUPERSEDED' WHERE id = $1", active.id
            )

        ref = await resolve_mandate_revision(pool, portfolio_id)

        assert ref is not None
        assert ref.status == MandateBindingStatus.EXPIRED
        assert ref.revision.id == active.id
    finally:
        await _cleanup(pool, mandate.id)


@pytest.mark.parametrize(
    "state",
    [
        MandateRevisionState.DRAFT,
        MandateRevisionState.PROPOSED,
        MandateRevisionState.SUPERSEDED,
        MandateRevisionState.CANCELLED,
    ],
)
def test_status_for_state_is_expired_for_every_non_active_non_paused_state(state) -> None:
    """총 함수(total function) 증명 -- ACTIVE/PAUSED가 아닌 6개 상태값 중
    남은 4개 전부가 (개수가 아니라 이름으로) EXPIRED로 떨어지는지 각각 확인한다.
    새 상태값이 `MandateRevisionState`에 추가돼도 이 분기는 기본값이 EXPIRED라
    자동으로 fail-closed다 -- 이 테스트는 그 기본 분기가 실제로 쓰이는지만
    검증한다."""
    assert _status_for_state(state) == MandateBindingStatus.EXPIRED


async def test_closed_pool_raises_instead_of_returning_none() -> None:
    """실패 주입 -- DB 커넥션이 죽으면 "없음"으로 오분류돼 호출부가 주문을
    조용히 통과시키는 게 아니라, 예외가 그대로 올라와야 한다(fail-closed).
    공유 `pool` 픽스처를 닫으면 그 픽스처의 teardown이 이미 닫힌 풀을 다시
    닫으려 해 다른 테스트에 영향을 주므로, 이 테스트 전용 풀을 따로 연다."""
    dedicated = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=1)
    await dedicated.close()
    with pytest.raises(asyncpg.InterfaceError):
        await resolve_mandate_revision(dedicated, uuid4())


async def test_resolve_latency_is_bounded(pool, repo) -> None:
    """성능 단언 -- 단일 조회는 인덱스된 FK 조인 하나뿐이라 로컬 Postgres에서
    수백 ms를 넘으면 회귀(예: 실수로 N+1 조회나 테이블 스캔이 섞였다는
    신호)다. 1초는 CI 공유 인스턴스의 커넥션 경합까지 감안한 넉넉한 상한."""
    tenant_id = await create_test_tenant(pool)
    portfolio_id = uuid4()
    mandate = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_id)
    try:
        draft = await repo.insert_draft_revision(
            mandate_id=mandate.id, revision_no=1, rules=_draft_revision(mandate.id)
        )
        await repo.activate_revision(mandate.id, draft.id, expected_active_revision_id=None)

        started = time.monotonic()
        ref = await resolve_mandate_revision(pool, portfolio_id)
        elapsed = time.monotonic() - started

        assert ref is not None
        assert elapsed < 1.0
    finally:
        await _cleanup(pool, mandate.id)
