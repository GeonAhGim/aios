"""PostgresTrustRepository 어댑터 직접 커버리지 — task-4626.

test_accept_and_revoke_consent.py는 application 레이어(accept_disclosure/
revoke_consent)를 통해 어댑터를 간접 호출한다. 그 경로로는 닿지 않는 어댑터
고유 분기(list_active_consents, insert_consent의 UniqueViolation 변환, revoke_consent의
LookupError/PermissionError)를 이 파일에서 직접 repo를 호출해 커버한다.
"""
# loc-allow: comprehensive single-adapter test suite covering all CRUD methods + failure injection

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.domain.models import Consent, ConsentState
from tests.foundation.integration.trust.conftest import create_disclosure, unique_purpose
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresTrustRepository:
    return PostgresTrustRepository(pool)


@pytest.fixture
def purpose() -> str:
    return unique_purpose()


async def _make_tenant(pool: asyncpg.Pool) -> UUID:
    return await create_test_tenant(pool)


# ============================================================================
# Tests for helper functions: _row_to_disclosure, _row_to_consent
# ============================================================================


async def test_row_to_disclosure_converts_record_from_db(pool: asyncpg.Pool) -> None:
    """_row_to_disclosure should convert asyncpg.Record to Disclosure domain object."""
    purpose = unique_purpose()
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM disclosure WHERE id = $1", disclosure_id)

    from src.foundation.trust.adapters.postgres_repository import _row_to_disclosure

    disclosure = _row_to_disclosure(row)

    assert disclosure.id == disclosure_id
    assert disclosure.purpose == purpose
    assert disclosure.revision == 1
    assert disclosure.content_hash is not None
    assert disclosure.published_at is not None
    assert disclosure.retired_at is None


async def test_row_to_consent_converts_record_from_db(pool: asyncpg.Pool) -> None:
    """_row_to_consent should convert asyncpg.Record to Consent domain object."""
    tenant_id = await create_test_tenant(pool)
    purpose = unique_purpose()
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO consent_record "
            "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision, state) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
            tenant_id,
            tenant_id,
            purpose,
            disclosure_id,
            1,
            "ACTIVE",
        )

    from src.foundation.trust.adapters.postgres_repository import _row_to_consent

    consent = _row_to_consent(row)

    assert consent.tenant_id == tenant_id
    assert consent.purpose == purpose
    assert consent.disclosure_id == disclosure_id
    assert consent.state == ConsentState.ACTIVE
    assert consent.accepted_at is not None


async def test_get_active_disclosure_returns_latest_revision(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """get_active_disclosure should return the highest revision disclosure."""
    # Create v1
    await create_disclosure(pool, purpose=purpose, revision=1)
    # Create v2 (higher revision)
    disclosure_id_v2 = await create_disclosure(pool, purpose=purpose, revision=2)

    result = await repo.get_active_disclosure(purpose)

    assert result is not None
    assert result.id == disclosure_id_v2
    assert result.revision == 2


async def test_get_active_disclosure_excludes_retired(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """negative: get_active_disclosure should skip retired disclosures."""
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    # Retire it
    async with pool.acquire() as conn:
        await conn.execute("UPDATE disclosure SET retired_at = now() WHERE id = $1", disclosure_id)

    result = await repo.get_active_disclosure(purpose)
    assert result is None


async def test_get_active_disclosure_returns_none_for_unknown_purpose(
    repo: PostgresTrustRepository, purpose: str
) -> None:
    result = await repo.get_active_disclosure(purpose)
    assert result is None


async def test_get_disclosure_by_purpose_and_revision_returns_tuple(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """get_disclosure_by_purpose_and_revision should return (Disclosure, datetime) tuple."""
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=2)

    result = await repo.get_disclosure_by_purpose_and_revision(purpose, 2)

    assert result is not None
    disclosure, server_now = result
    assert disclosure.id == disclosure_id
    assert disclosure.revision == 2
    assert server_now is not None


async def test_get_disclosure_by_purpose_and_revision_returns_none_for_missing_revision(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    await create_disclosure(pool, purpose=purpose, revision=1)

    result = await repo.get_disclosure_by_purpose_and_revision(purpose, 99)

    assert result is None


async def test_get_active_consent_returns_active_consent(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """get_active_consent should return ACTIVE consent for tenant/purpose."""
    tenant_id = await _make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    result = await repo.get_active_consent(tenant_id, purpose)

    assert result is not None
    assert result.state == ConsentState.ACTIVE
    assert result.purpose == purpose


async def test_get_active_consent_ignores_revoked(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """negative: get_active_consent should skip REVOKED consents."""
    tenant_id = await _make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    consent = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )
    await repo.revoke_consent(consent.id, tenant_id=tenant_id)

    result = await repo.get_active_consent(tenant_id, purpose)
    assert result is None


async def test_get_active_consent_returns_none_when_none_exists(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await _make_tenant(pool)

    result = await repo.get_active_consent(tenant_id, purpose)

    assert result is None


async def test_get_latest_consent_returns_none_when_none_exists(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await _make_tenant(pool)

    result = await repo.get_latest_consent(tenant_id, purpose)

    assert result is None


async def test_get_latest_consent_returns_most_recently_accepted(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await _make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    first = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )
    await repo.revoke_consent(first.id, tenant_id=tenant_id)

    disclosure_id_v2 = await create_disclosure(pool, purpose=purpose, revision=2)
    second = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id_v2,
        disclosure_revision=2,
        expires_at=None,
    )

    latest = await repo.get_latest_consent(tenant_id, purpose)

    assert latest is not None
    assert latest.id == second.id
    assert latest.disclosure_revision == 2


async def test_list_active_consents_returns_empty_for_tenant_without_consents(
    pool: asyncpg.Pool, repo: PostgresTrustRepository
) -> None:
    tenant_id = await _make_tenant(pool)

    result = await repo.list_active_consents(tenant_id)

    assert result == []


async def test_list_active_consents_returns_only_active_rows(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await _make_tenant(pool)
    disclosure_id_1 = await create_disclosure(pool, purpose=purpose, revision=1)
    active = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id_1,
        disclosure_revision=1,
        expires_at=None,
    )

    other_purpose = unique_purpose()
    disclosure_id_2 = await create_disclosure(pool, purpose=other_purpose, revision=1)
    revoked_target = await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=other_purpose,
        disclosure_id=disclosure_id_2,
        disclosure_revision=1,
        expires_at=None,
    )
    await repo.revoke_consent(revoked_target.id, tenant_id=tenant_id)

    result = await repo.list_active_consents(tenant_id)

    assert [c.id for c in result] == [active.id]
    assert result[0].state == ConsentState.ACTIVE


async def test_insert_consent_concurrent_race_translates_unique_violation(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """105번 §2.2 — uq_consent_record_active_purpose 위반을 어댑터가 직접
    ConcurrencyConflictError로 번역하는 분기(라인 126-133)는 application 레이어의
    사전 존재 체크를 우회해야만 닿는다. repo.insert_consent()를 동시에 두 번
    호출해 DB 유니크 인덱스 위반을 강제로 재현한다."""
    tenant_id = await _make_tenant(pool)
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
    tenant_id = await _make_tenant(pool)
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
    tenant_id = await _make_tenant(pool)
    missing_id = uuid4()

    with pytest.raises(LookupError):
        await repo.revoke_consent(missing_id, tenant_id=tenant_id)


async def test_revoke_consent_already_revoked_raises_concurrency_error(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """negative: revoke_consent should raise ConcurrencyConflictError if state
    has already been changed from ACTIVE to REVOKED (conditional_update fails)."""
    tenant_id = await _make_tenant(pool)
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
    owner_tenant_id = await _make_tenant(pool)
    other_tenant_id = await _make_tenant(pool)
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
    tenant_id = await _make_tenant(pool)
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
    tenant_id = await _make_tenant(pool)
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
    tenant_id = await _make_tenant(pool)
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


@pytest.mark.perf
async def test_get_active_consent_read_p95_latency_within_budget(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """ADR-2026-09-09-C 예산표 — 단순 인덱스 조회(FA 축)는 p95 50ms 이하를 기대한다."""
    tenant_id = await _make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    await repo.insert_consent(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=disclosure_id,
        disclosure_revision=1,
        expires_at=None,
    )

    samples = []
    for _ in range(20):
        start = time.perf_counter()
        await repo.get_active_consent(tenant_id, purpose)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 0.05, f"get_active_consent p95={p95:.4f}s exceeds 50ms budget"
