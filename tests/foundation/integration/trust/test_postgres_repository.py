"""PostgresTrustRepository 어댑터 직접 커버리지 (조회 경로) — task-4626 / task-10865.

test_accept_and_revoke_consent.py는 application 레이어(accept_disclosure/
revoke_consent)를 통해 어댑터를 간접 호출한다. 그 경로로는 닿지 않는 어댑터
고유 분기(list_active_consents 등)를 이 파일에서 직접 repo를 호출해 커버한다.

이 파일은 _row_to_*·get_active_disclosure·get_disclosure_by_purpose_and_revision·
get_active_consent·get_latest_consent·list_active_consents 등 조회 경로만 다룬다.
insert_consent/revoke_consent 변이·실패주입·RLS·성능 테스트는
test_postgres_repository_mutations.py로 분리했다(500줄 경고 분할, task-10865).
"""

from __future__ import annotations

import asyncpg

from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.domain.models import ConsentState
from tests.foundation.integration.trust.conftest import (
    create_disclosure,
    make_tenant,
    unique_purpose,
)
from tests.integration.conftest import create_test_tenant

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

    result = await repo.get_active_consent(tenant_id, purpose)

    assert result is not None
    assert result.state == ConsentState.ACTIVE
    assert result.purpose == purpose


async def test_get_active_consent_ignores_revoked(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    """negative: get_active_consent should skip REVOKED consents."""
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
    await repo.revoke_consent(consent.id, tenant_id=tenant_id)

    result = await repo.get_active_consent(tenant_id, purpose)
    assert result is None


async def test_get_active_consent_returns_none_when_none_exists(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await make_tenant(pool)

    result = await repo.get_active_consent(tenant_id, purpose)

    assert result is None


async def test_get_latest_consent_returns_none_when_none_exists(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await make_tenant(pool)

    result = await repo.get_latest_consent(tenant_id, purpose)

    assert result is None


async def test_get_latest_consent_returns_most_recently_accepted(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await make_tenant(pool)
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
    tenant_id = await make_tenant(pool)

    result = await repo.list_active_consents(tenant_id)

    assert result == []


async def test_list_active_consents_returns_only_active_rows(
    pool: asyncpg.Pool, repo: PostgresTrustRepository, purpose: str
) -> None:
    tenant_id = await make_tenant(pool)
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
