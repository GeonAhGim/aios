"""F1(task-9420/task-9458, tier M) — `PostgresAuditEventRepository`가 실제로
`app.tenant_id` GUC를 바인딩하는지 검증한다.

감사 재현(docs/audits/AUDIT_2026-09-30_auth_rls.md F1): `foundation_audit_event`는
RLS ENABLE+FORCE(b3c7f19ad2e6/c9f4e2a1b6d7)가 걸려 있는데, 이 파일의 구현체가
`pool.acquire()`만 쓰고 `tenant_transaction()`(GUC 바인딩)을 거치지 않아 — 이
환경의 DATABASE_URL 롤은 슈퍼유저(rolbypassrls=true)라 지금은 드러나지 않지만
— 운영에서 non-superuser 롤(`aios_app`)로 전환되는 순간 정상 테넌트도 자기
audit event를 조회/삽입하지 못하게 된다(GUC 없이 조회 → None/0행, GUC 주입 후
동일 조회 → 1행).

`tests/integration/core/db/test_rls_foundation.py`처럼 `AppRoleTx`로 SQL을
직접 실행해 우회하지 않는다 — `tests/foundation/integration/mandates/
test_postgres_repository_rls.py`/`tests/foundation/integration/reconciliation/
test_postgres_repository.py`와 동일하게, 실제 `PostgresAuditEventRepository`
메서드 호출 경로(`append_event`/`list_timeline`/`list_chain_for_verification`)를
그대로 타면서 그 메서드가 내부적으로 여는 커넥션의 역할만 `aios_app`으로
낮춘다(`_AppRolePool`, 아래).
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Classification, Outcome
from tests.integration.conftest import create_test_tenant

# task-9456 F1 DEEPEN(mandates/reconciliation test_postgres_repository_rls.py와
# 동일 차용 근거)과 같은 예산 — 전용 예산 항목이 없는 단일 실DB 왕복에 "주문
# 제출->ACK p95 50ms(paper)"를 차용한다.
_RLS_APPEND_P95_BUDGET_MS = 50.0


class _AppRolePool:
    """실제 `pool`을 감싸, `acquire()`가 내주는 커넥션의 역할만 `aios_app`으로
    낮춘다 — `PostgresAuditEventRepository`는 이 객체를 평범한 `asyncpg.Pool`
    처럼 쓰므로(`tenant_transaction`/`system_transaction`이 `.acquire()`만
    호출), 리포지토리 코드는 한 줄도 바뀌지 않는다.

    `aios_app`은 LOGIN 권한이 없어 별도 자격증명으로 접속할 수 없으므로,
    슈퍼유저 커넥션 안에서 매 acquire마다 `SET ROLE aios_app`을 걸고 반환 전
    `RESET ROLE`로 되돌린다."""

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
async def pool() -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(dsn=_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresAuditEventRepository:
    return PostgresAuditEventRepository(pool)


async def _tenant(pool: asyncpg.Pool) -> UUID:
    return await create_test_tenant(pool)


def _append_kwargs(tenant_id: UUID | None, **overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = dict(
        tenant_id=tenant_id,
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="rls_probe",
        outcome=Outcome.SUCCESS,
        actor_subject_id=tenant_id,
        trace_id=uuid4(),
        payload_hash="0" * 64,
        payload={},
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return defaults


# --- F1-negative-1: GUC 미바인딩 -> 0행/거부 ------------------------------


async def test_append_event_fails_closed_when_guc_binding_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pool: asyncpg.Pool
) -> None:
    """실패주입 + 적색 게이트 재현: F1이 수정 전에 실제로 겪던 증상을 그대로
    재현한다 — `tenant_transaction`이 걸리지 않으면(`SET ROLE aios_app`만 걸린
    평범한 `pool.acquire()`와 동일한 상태), `aios_app` 롤 아래의 INSERT는
    `foundation_audit_event`의 RLS `WITH CHECK`를 통과하지 못해 예외로
    fail-closed 되어야 한다 — 조용히 다른 테넌트 소유로 잘못 쓰이거나 성공한
    척하지 않는다."""

    @asynccontextmanager
    async def _unbound_tenant_transaction(
        pool_arg: asyncpg.Pool, _tenant_id: UUID | None
    ) -> AsyncIterator[asyncpg.Connection]:
        async with pool_arg.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.evidence.adapters.postgres_repository.tenant_transaction",
        _unbound_tenant_transaction,
    )
    tenant_id = await _tenant(pool)
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_role_repo.append_event(**_append_kwargs(tenant_id))


async def test_list_chain_for_verification_returns_zero_rows_when_guc_binding_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pool: asyncpg.Pool, repo: PostgresAuditEventRepository
) -> None:
    """감사 재현 전반부("GUC 없이 조회 -> 0행"): superuser 경로(`repo`)로 먼저
    이벤트를 하나 심어 두고, GUC 바인딩이 빠진 `aios_app` 경로로 같은 tenant의
    체인을 조회하면 0행이어야 한다."""

    @asynccontextmanager
    async def _unbound_tenant_transaction(
        pool_arg: asyncpg.Pool, _tenant_id: UUID | None
    ) -> AsyncIterator[asyncpg.Connection]:
        async with pool_arg.acquire() as conn, conn.transaction():
            yield conn

    tenant_id = await _tenant(pool)
    await repo.append_event(**_append_kwargs(tenant_id))

    monkeypatch.setattr(
        "src.foundation.evidence.adapters.postgres_repository.tenant_transaction",
        _unbound_tenant_transaction,
    )
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    events = await app_role_repo.list_chain_for_verification(tenant_id)

    assert events == []


# --- F1-negative-2: tenant A GUC로 tenant B 행 조회 -> 0행 (교차 격리) ----


async def test_list_chain_for_verification_cross_tenant_returns_nothing(
    pool: asyncpg.Pool, repo: PostgresAuditEventRepository
) -> None:
    """negative(교차 테넌트): tenant A의 GUC로 tenant B의 체인을 조회해도
    0행이어야 한다 — WHERE의 tenant_id=A와 GUC로 바인딩된 app.tenant_id=A가
    이중으로 B의 행을 걸러낸다."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    await repo.append_event(**_append_kwargs(tenant_b))
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    events = await app_role_repo.list_chain_for_verification(tenant_a)

    assert events == []


async def test_list_timeline_cross_tenant_returns_nothing(
    pool: asyncpg.Pool, repo: PostgresAuditEventRepository
) -> None:
    """negative(교차 테넌트, 별도 메서드): `list_timeline`도 같은 이중 방어를
    지켜야 한다."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    await repo.append_event(**_append_kwargs(tenant_b))
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    items, next_cursor = await app_role_repo.list_timeline(tenant_a, cursor=None, limit=50)

    assert items == []
    assert next_cursor is None


# --- F1-positive: tenant_transaction()/system_transaction() 바인딩 -> 정상 조회 ---


async def test_append_event_and_list_chain_visible_when_tenant_guc_is_bound(
    pool: asyncpg.Pool,
) -> None:
    """감사 재현 후반부("GUC 주입 후 동일 조회 1행") — `aios_app` 롤 아래에서도
    append + list_chain_for_verification이 실제 repository 호출 경로만으로
    정상 테넌트의 이벤트를 1행으로 돌려주는지 확인한다."""
    tenant_id = await _tenant(pool)
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    appended = await app_role_repo.append_event(**_append_kwargs(tenant_id))
    events = await app_role_repo.list_chain_for_verification(tenant_id)

    assert appended.sequence_no == 1
    assert [e.id for e in events] == [appended.id]


async def test_append_system_event_visible_under_system_transaction(
    pool: asyncpg.Pool,
) -> None:
    """system 체인(`tenant_id=None`)은 `system_transaction()`으로 바인딩된다
    — `aios_app` 롤 아래에서도 append 직후 자신의 system 이벤트를 다시 읽을 수
    있어야 한다(`tenant_id IS NULL AND app.role='system'` 예외 분기)."""
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    appended = await app_role_repo.append_event(**_append_kwargs(None))
    events = await app_role_repo.list_chain_for_verification(None)

    assert appended.tenant_id is None
    assert appended.id in [e.id for e in events]


async def _append_p95_ms(
    app_role_repo: PostgresAuditEventRepository, tenant_id: UUID, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await app_role_repo.append_event(**_append_kwargs(tenant_id))
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_append_event_under_app_role_p95_under_borrowed_order_ack_budget(
    pool: asyncpg.Pool,
) -> None:
    """수치 성능 단언: `tenant_transaction` + `SET ROLE aios_app` + advisory
    lock + RLS 정책 평가를 포함한 단일 실DB 왕복의 p95가 예산(위 상수) 이내인지
    30회 반복으로 확인한다."""
    tenant_id = await _tenant(pool)
    app_role_repo = PostgresAuditEventRepository(_AppRolePool(pool))

    p95_ms = await _append_p95_ms(app_role_repo, tenant_id, n=30)

    assert p95_ms < _RLS_APPEND_P95_BUDGET_MS
