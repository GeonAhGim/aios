"""PostgresTrustRepository 어댑터 직접 커버리지 (변이·실패주입·RLS·성능) — task-10865.

test_postgres_repository.py에서 조회 경로(get_active_disclosure 등)를 분리한
나머지 절반: insert_consent/revoke_consent의 변이 경로, UniqueViolation/FK
위반 등 실패주입, AUDIT_2026-09-30_auth_rls.md F1(GUC 바인딩) 회귀, 성능
예산(p95) 검증을 다룬다(500줄 경고 분할, task-10865).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.domain.models import Consent, ConsentState
from tests.foundation.integration.trust.conftest import create_disclosure, make_tenant

# ============================================================================
# insert_consent / revoke_consent mutation tests
# ============================================================================


async def test_insert_consent_concurrent_race_translates_unique_violation(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """105번 §2.2 — uq_consent_record_active_purpose 위반을 어댑터가 직접
    ConcurrencyConflictError로 번역하는 분기(라인 126-133)는 application 레이어의
    사전 존재 체크를 우회해야만 닿는다. repo.insert_consent()를 동시에 두 번
    호출해 DB 유니크 인덱스 위반을 강제로 재현한다."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    async def attempt() -> Consent:
        return await repo.insert_consent(
            tenant_id=tenant_id,
            subject_id=tenant_id,
            purpose=purpose,
            disclosure_id=disclosure_id,
            disclosure_revision=1,
            expires_at=None,
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert str(tenant_id) in str(failures[0])


async def test_revoke_consent_changes_state_to_revoked(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """revoke_consent should change consent state from ACTIVE to REVOKED."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    consent = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    result = await repo.revoke_consent(consent.id, tenant_id=tenant_id)

    assert result.state == ConsentState.REVOKED
    assert result.revoked_at is not None

    # Verify state persisted in DB
    active = await repo.get_active_consent(tenant_id, purpose)
    assert active is None


async def test_revoke_consent_unknown_id_raises_lookup_error(
    pool: asyncpg.Pool, repo: PostgresTrustRepository
) -> None:
    tenant_id = await make_tenant(pool)
    missing_id = uuid4()

    with pytest.raises(LookupError):
        await repo.revoke_consent(missing_id, tenant_id=tenant_id)


async def test_revoke_consent_already_revoked_raises_concurrency_error(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """negative: revoke_consent should raise ConcurrencyConflictError if state
    has already been changed from ACTIVE to REVOKED (conditional_update fails)."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    consent = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    # First revoke should succeed
    await repo.revoke_consent(consent.id, tenant_id=tenant_id)

    # Second revoke should fail (state is now REVOKED, not ACTIVE)
    with pytest.raises(ConcurrencyConflictError):
        await repo.revoke_consent(consent.id, tenant_id=tenant_id)


async def test_revoke_consent_wrong_tenant_raises_permission_error(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    owner_tenant_id = await make_tenant(pool)
    other_tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    consent = await repo.insert_consent(
        tenant_id=owner_tenant_id,
        subject_id=owner_tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    with pytest.raises(PermissionError):
        await repo.revoke_consent(consent.id, tenant_id=other_tenant_id)

    # unaffected by the rejected cross-tenant attempt
    still_active = await repo.get_active_consent(owner_tenant_id, purpose)
    assert still_active is not None
    assert still_active.id == consent.id


# ============================================================================
# Failure injection tests
# ============================================================================


async def test_insert_consent_propagates_fk_violation_on_invalid_disclosure(
    pool: asyncpg.Pool, repo: PostgresTrustRepository
) -> None:
    """실패주입: insert_consent should propagate FK violation if
    disclosure_id doesn't exist."""
    tenant_id = await make_tenant(pool)
    non_existent_disclosure_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.insert_consent(
            tenant_id=tenant_id,
            subject_id=tenant_id,
            purpose="test-purpose",
            disclosure_id=non_existent_disclosure_id,
            disclosure_revision=1,
            expires_at=None,
        )


async def test_revoke_consent_integrates_conditional_update_correctly(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입: revoke_consent should call conditional_update with correct
    parameters and propagate its exceptions (verifies integration point)."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    consent = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    # Track conditional_update call to verify it's invoked
    call_count = [0]
    from src.core.db.conditional_write import conditional_update as original_cu

    async def tracked_conditional_update(*args: Any, **kwargs: Any) -> asyncpg.Record:
        call_count[0] += 1
        return await original_cu(*args, **kwargs)

    monkeypatch.setattr(
        "src.foundation.trust.adapters.postgres_repository.conditional_update",
        tracked_conditional_update,
    )

    result = await repo.revoke_consent(consent.id, tenant_id=tenant_id)

    assert call_count[0] == 1
    assert result.state == ConsentState.REVOKED


async def test_insert_consent_propagates_concurrency_conflict_when_duplicate(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """실패주입: insert_consent should raise ConcurrencyConflictError when
    trying to insert duplicate ACTIVE consent (translates asyncpg.UniqueViolationError)."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    # First insert succeeds
    await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    # Second insert on same purpose should raise ConcurrencyConflictError
    # (due to uq_consent_record_active_purpose unique index)
    with pytest.raises(ConcurrencyConflictError):
        await repo.insert_consent(
            tenant_id=tenant_id,
            subject_id=tenant_id,
            purpose=purpose,
            disclosure_id=disclosure_id,
            disclosure_revision=1,
            expires_at=None,
        )


# ============================================================================
# AUDIT_2026-09-30_auth_rls.md F1 (task-9453) — tenant_transaction/GUC binding
# ============================================================================


async def test_get_active_consent_returns_none_under_aios_app_role_when_guc_unbound(
    pool: asyncpg.Pool,
    aios_app_repo: PostgresTrustRepository,
    purpose: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative / F1 재현: 감사가 지적한 정정 전 상태(GUC 미바인딩)를 회귀
    테스트로 고정한다. tenant_transaction을 GUC를 세팅하지 않는 `pool.acquire()`
    동급 버전으로 몽키패치해 정정 전 어댑터를 재현하면, 비-superuser `aios_app`
    role 아래에서는 소유 tenant 자신의 ACTIVE 동의조차 0행(None)으로 막힌다 —
    RLS 정책이 SQL의 tenant 조건이 아니라 이 GUC로 판정하기 때문이다."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO consent_record "
            "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
            "VALUES ($1, $1, $2, $3, 1)",
            tenant_id,
            purpose,
            disclosure_id,
        )

    @asynccontextmanager
    async def _unbound_transaction(pool: asyncpg.Pool, tenant_id: UUID | None):
        # F1 정정 전 어댑터와 동급: 연결은 열지만 app.tenant_id GUC를 세팅하지 않는다.
        async with pool.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.trust.adapters.postgres_repository.tenant_transaction",
        _unbound_transaction,
    )

    result = await aios_app_repo.get_active_consent(tenant_id, purpose)

    assert result is None  # F1: GUC 미바인딩이면 자기 행도 0행


async def test_get_active_consent_returns_row_under_aios_app_role_once_guc_bound(
    pool: asyncpg.Pool, aios_app_repo: PostgresTrustRepository, purpose: str
) -> None:
    """감사 재현의 나머지 절반: 몽키패치 없이(즉 정정된 어댑터로) 같은 조회를
    같은 aios_app role에서 실행하면 GUC가 바인딩되어 1행이 돌아온다 — F1이
    실제로 고쳐졌다는 양성 증거."""
    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO consent_record "
            "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
            "VALUES ($1, $1, $2, $3, 1)",
            tenant_id,
            purpose,
            disclosure_id,
        )

    result = await aios_app_repo.get_active_consent(tenant_id, purpose)

    assert result is not None
    assert result.tenant_id == tenant_id


async def test_get_active_consent_cross_tenant_returns_none_under_aios_app_role(
    pool: asyncpg.Pool, aios_app_repo: PostgresTrustRepository, purpose: str
) -> None:
    """negative: tenant A GUC로 tenant B의 행을 조회하면 0행이어야 한다
    (교차 테넌트 격리) — WHERE tenant_id 조건과 RLS 정책이 이중으로 막는다."""
    tenant_a = await make_tenant(pool)
    tenant_b = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO consent_record "
            "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
            "VALUES ($1, $1, $2, $3, 1)",
            tenant_a,
            purpose,
            disclosure_id,
        )

    result = await aios_app_repo.get_active_consent(tenant_b, purpose)

    assert result is None


@pytest.mark.perf
async def test_get_active_consent_read_p95_latency_within_budget(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str, perf_budget
) -> None:
    """ADR-2026-09-09-C 예산표 — 단순 인덱스 조회(FA 축)는 p95 50ms 이하를 기대한다.

    raw time.perf_counter() → perf_budget.sample_async() 전환(task-10898).
    """

    async def _read_once() -> None:
        await repo.get_active_consent(tenant_id, purpose)

    tenant_id = await make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    samples = sorted(
        [await perf_budget.sample_async(_read_once) for _ in range(20)],
        key=lambda s: s.wall_ms,
    )
    p95 = samples[int(len(samples) * 0.95) - 1].wall_ms
    assert p95 < 50.0, f"get_active_consent p95={p95:.3f}ms exceeds 50ms budget"
