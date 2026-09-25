"""PostgresTrustRepository 어댑터 직접 커버리지 — task-4626.

test_accept_and_revoke_consent.py는 application 레이어(accept_disclosure/
revoke_consent)를 통해 어댑터를 간접 호출한다. 그 경로로는 닿지 않는 어댑터
고유 분기(list_active_consents, insert_consent의 UniqueViolation 변환, revoke_consent의
LookupError/PermissionError)를 이 파일에서 직접 repo를 호출해 커버한다.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.domain.models import ConsentState
from tests.foundation.integration.trust.conftest import create_disclosure, unique_purpose
from tests.integration.conftest import create_test_tenant


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
    return PostgresTrustRepository(pool)


@pytest.fixture
def purpose():
    return unique_purpose()


async def _make_tenant(pool):
    return await create_test_tenant(pool)


async def test_get_active_disclosure_returns_none_for_unknown_purpose(repo, purpose):
    result = await repo.get_active_disclosure(purpose)
    assert result is None


async def test_get_disclosure_by_purpose_and_revision_returns_none_for_missing_revision(
    pool, repo, purpose
):
    await create_disclosure(pool, purpose=purpose, revision=1)

    result = await repo.get_disclosure_by_purpose_and_revision(purpose, 99)

    assert result is None


async def test_get_active_consent_returns_none_when_none_exists(pool, repo, purpose):
    tenant_id = await _make_tenant(pool)

    result = await repo.get_active_consent(tenant_id, purpose)

    assert result is None


async def test_get_latest_consent_returns_none_when_none_exists(pool, repo, purpose):
    tenant_id = await _make_tenant(pool)

    result = await repo.get_latest_consent(tenant_id, purpose)

    assert result is None


async def test_get_latest_consent_returns_most_recently_accepted(pool, repo, purpose):
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


async def test_list_active_consents_returns_empty_for_tenant_without_consents(pool, repo):
    tenant_id = await _make_tenant(pool)

    result = await repo.list_active_consents(tenant_id)

    assert result == []


async def test_list_active_consents_returns_only_active_rows(pool, repo, purpose):
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


async def test_insert_consent_concurrent_race_translates_unique_violation(pool, repo, purpose):
    """105번 §2.2 — uq_consent_record_active_purpose 위반을 어댑터가 직접
    ConcurrencyConflictError로 번역하는 분기(라인 126-133)는 application 레이어의
    사전 존재 체크를 우회해야만 닿는다. repo.insert_consent()를 동시에 두 번
    호출해 DB 유니크 인덱스 위반을 강제로 재현한다."""
    tenant_id = await _make_tenant(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    async def attempt():
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


async def test_revoke_consent_unknown_id_raises_lookup_error(pool, repo):
    tenant_id = await _make_tenant(pool)
    missing_id = uuid4()

    with pytest.raises(LookupError):
        await repo.revoke_consent(missing_id, tenant_id=tenant_id)


async def test_revoke_consent_wrong_tenant_raises_permission_error(pool, repo, purpose):
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


@pytest.mark.perf
async def test_get_active_consent_read_p95_latency_within_budget(pool, repo, purpose):
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
