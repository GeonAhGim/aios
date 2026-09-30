"""F1(task-9454, tier M) — `PostgresMandateRepository`가 실제로 `app.tenant_id`
GUC를 바인딩하는지 검증한다.

감사 재현(docs/audits/AUDIT_2026-09-30_auth_rls.md F1): `portfolio_mandate`는
RLS ENABLE+FORCE(b3c7f19ad2e6/c9f4e2a1b6d7)가 걸려 있는데, 이 파일의 구현체가
`pool.acquire()`만 쓰고 `tenant_transaction()`(GUC 바인딩)을 거치지 않아 — 이
환경의 DATABASE_URL 롤은 슈퍼유저(rolbypassrls=true)라 지금은 드러나지 않지만
— 운영에서 non-superuser 롤(`aios_app`)로 전환되는 순간 정상 테넌트도 자기
mandate를 조회하지 못하게 된다(GUC 없이 조회 → None, GUC 주입 후 동일 조회
→ 1행).

이 파일은 `tests/integration/core/db/test_rls_foundation.py`처럼 `AppRoleTx`로
SQL을 직접 실행해 우회하지 않는다 — 실제 `PostgresMandateRepository` 메서드
호출 경로(`get_mandate`/`get_or_create_mandate`/`activate_revision`)를 그대로
타면서, 그 메서드가 내부적으로 여는 커넥션의 역할만 `aios_app`으로 낮춘다
(`_AppRolePool`, 아래).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.domain.models import Autonomy, MandateRevision, MandateRevisionState
from tests.integration.conftest import create_test_tenant

# task-3160/3162/3168/3169 DEEPEN과 동일 차용 근거(전용 예산 항목이 없는 단일
# 실DB 왕복에 "주문 제출->ACK p95 50ms(paper)"를 차용) — test_rls_foundation.py의
# _RLS_SELECT_P95_BUDGET_MS와 같은 값.
_RLS_SELECT_P95_BUDGET_MS = 50.0


class _AppRolePool:
    """실제 `pool`을 감싸, `acquire()`가 내주는 커넥션의 역할만 `aios_app`으로
    낮춘다 — `PostgresMandateRepository`는 이 객체를 평범한 `asyncpg.Pool`처럼
    쓰므로(`.acquire()`만 호출), 리포지토리 코드는 한 줄도 바뀌지 않는다.

    `aios_app`은 LOGIN 권한이 없어(conftest 기존 `AppRoleTx`와 동일 이유) 별도
    자격증명으로 접속할 수 없으므로, 슈퍼유저 커넥션 안에서 매 acquire마다
    `SET ROLE aios_app`을 걸고 반환 전 `RESET ROLE`로 되돌린다 — 이 프로세스가
    쓰는 `pool` 픽스처는 이 테스트 파일 전용(conftest의 `pool`)이라 다른 테스트로
    새지 않지만, 커넥션 자체는 풀에 반환되어 재사용되므로 `RESET ROLE`을 생략하지
    않는다.
    """

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


async def _cleanup(pool: asyncpg.Pool, mandate_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE portfolio_mandate SET active_revision_id = NULL WHERE id = $1", mandate_id
        )
        await conn.execute("DELETE FROM mandate_revision WHERE mandate_id = $1", mandate_id)
        await conn.execute("DELETE FROM portfolio_mandate WHERE id = $1", mandate_id)


async def test_get_mandate_returns_row_when_tenant_guc_is_bound(pool, repo) -> None:
    """감사 재현의 후반부("GUC 주입 후 동일 조회 1행") — 이 리프의 수정
    (`tenant_transaction` 경유)이 실제로 `aios_app` 롤 아래에서도 정상 테넌트의
    조회를 1행으로 되돌리는지 확인한다."""
    tenant_a = await create_test_tenant(pool)
    portfolio_id = default_portfolio_id(tenant_a)
    mandate = await repo.get_or_create_mandate(tenant_a, tenant_a, portfolio_id=portfolio_id)
    try:
        app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

        found = await app_role_repo.get_mandate(tenant_a, portfolio_id)

        assert found is not None
        assert found.id == mandate.id
    finally:
        await _cleanup(pool, mandate.id)


async def test_get_mandate_cross_tenant_portfolio_guess_returns_nothing(pool, repo) -> None:
    """negative(교차 테넌트): `default_portfolio_id`는 user_id의 결정적 함수라
    ([[src/foundation/entities/domain/defaults.py]]) tenant B의 id를 아는
    tenant A는 B의 portfolio_id를 그대로 계산할 수 있다 — `tenant_id=A`로
    B의 portfolio_id를 조회 시도해도(WHERE의 tenant_id=A와 A로 바인딩된
    `app.tenant_id` GUC가 이중으로) 0행이어야 한다."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    mandate_b = await repo.get_or_create_mandate(
        tenant_b, tenant_b, portfolio_id=default_portfolio_id(tenant_b)
    )
    try:
        app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

        found = await app_role_repo.get_mandate(tenant_a, default_portfolio_id(tenant_b))

        assert found is None
    finally:
        await _cleanup(pool, mandate_b.id)


async def test_get_mandate_fails_closed_when_guc_binding_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pool, repo
) -> None:
    """실패주입 + 적색 게이트 재현: F1이 수정 전에 실제로 겪던 증상을
    그대로 재현한다 — `tenant_transaction`이 걸리지 않으면(`SET ROLE
    aios_app`만 걸린 평범한 `pool.acquire()`와 동일한 상태), 정상 테넌트가
    자기 행을 조회해도 None이어야 한다(0행/거부). 이 테스트는 실제
    `get_mandate` 호출 경로를 타되, 그 안에서 쓰는
    `tenant_transaction`만 GUC를 걸지 않는 가짜로 바꿔치기해 "바인딩이
    빠지면 무엇이 깨지는가"를 고정한다 — 고쳐진 코드가 이 몽키패치 없이는
    통과하지 못했던 상태(fail-open이 아니라 fail-closed)를 회귀로 잠근다."""
    tenant_a = await create_test_tenant(pool)
    portfolio_id = default_portfolio_id(tenant_a)
    mandate = await repo.get_or_create_mandate(tenant_a, tenant_a, portfolio_id=portfolio_id)
    try:

        @asynccontextmanager
        async def _unbound_tenant_transaction(
            pool_arg: asyncpg.Pool, _tenant_id: UUID | None
        ) -> AsyncIterator[asyncpg.Connection]:
            async with pool_arg.acquire() as conn, conn.transaction():
                yield conn

        monkeypatch.setattr(
            "src.foundation.mandates.adapters.postgres_repository.tenant_transaction",
            _unbound_tenant_transaction,
        )
        app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

        found = await app_role_repo.get_mandate(tenant_a, portfolio_id)

        assert found is None
    finally:
        await _cleanup(pool, mandate.id)


async def test_activate_revision_write_fails_closed_without_tenant_guc(pool, repo) -> None:
    """negative: `activate_revision()`의 `portfolio_mandate` UPDATE는 (이
    리프에서 의도적으로 남겨둔 범위 밖 — 모듈 docstring 참조) 여전히
    `tenant_transaction`을 거치지 않는다. `aios_app` 롤 아래에서 GUC가 전혀
    바인딩되지 않으면 RLS 정책이 모든 행을 걸러내 0행 UPDATE가 되고,
    `activate_revision`의 existence-check SELECT도 같은 이유로 0행을 봐
    `LookupError`(mandate가 존재하지 않는다는 오탐)로 끝난다 — 정상 테넌트의
    자기 mandate를 cross-tenant 공격과 구분 없이 그냥 거부하는 fail-closed
    상태를 회귀로 고정한다."""
    tenant_a = await create_test_tenant(pool)
    portfolio_id = default_portfolio_id(tenant_a)
    mandate = await repo.get_or_create_mandate(tenant_a, tenant_a, portfolio_id=portfolio_id)
    draft = await repo.insert_draft_revision(
        mandate_id=mandate.id, revision_no=1, rules=_draft_revision(mandate.id)
    )
    try:
        app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

        with pytest.raises((LookupError, ConcurrencyConflictError)):
            await app_role_repo.activate_revision(
                mandate.id, draft.id, expected_active_revision_id=None
            )
    finally:
        await _cleanup(pool, mandate.id)


async def _get_mandate_p95_ms(
    app_role_repo: PostgresMandateRepository, tenant_id: UUID, portfolio_id: UUID, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await app_role_repo.get_mandate(tenant_id, portfolio_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_get_mandate_under_app_role_p95_under_borrowed_order_ack_budget(pool, repo) -> None:
    """수치 성능 단언: `tenant_transaction` + `SET ROLE aios_app` + RLS 정책
    평가를 포함한 단일 실DB 왕복의 p95가 예산(위 상수, test_rls_foundation.py와
    동일 차용 근거) 이내인지 30회 반복으로 확인한다."""
    tenant_a = await create_test_tenant(pool)
    portfolio_id = default_portfolio_id(tenant_a)
    mandate = await repo.get_or_create_mandate(tenant_a, tenant_a, portfolio_id=portfolio_id)
    try:
        app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

        p95_ms = await _get_mandate_p95_ms(app_role_repo, tenant_a, portfolio_id, n=30)

        assert p95_ms < _RLS_SELECT_P95_BUDGET_MS
    finally:
        await _cleanup(pool, mandate.id)
