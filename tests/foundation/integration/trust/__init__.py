"""FND-01 consent rejection wiring (task-7957; INVARIANTS I-07/I-10).

Explicitly collected by the assigned task command; repository failures must never
produce a successful consent or silently persist a partial acceptance.
"""
from __future__ import annotations

import os
from time import perf_counter
from unittest.mock import AsyncMock

import asyncpg
import pytest

from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.accept_disclosure import (
    ConsentAlreadyActiveError,
    DisclosureNotFoundError,
    DisclosureRetiredError,
    accept_disclosure,
)
from src.foundation.trust.application.evaluate_trust_freshness import evaluate_trust_freshness
from src.foundation.trust.contracts.v1 import TenantContext
from tests.foundation.integration.trust.conftest import (
    create_disclosure,
    retire_disclosure,
    unique_purpose,
)
from tests.integration.conftest import create_test_tenant


@pytest.fixture
async def consent_case():
    dsn = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        tenant_id = await create_test_tenant(pool)
        context = TenantContext(
            tenant_id=tenant_id, subject_id=tenant_id, mfa_verified=False
        )
        yield pool, PostgresTrustRepository(pool), context, unique_purpose()
    finally:
        await pool.close()


async def test_negative_unknown_revision_leaves_no_consent(consent_case):
    pool, repo, context, purpose = consent_case
    await create_disclosure(pool, purpose=purpose)
    with pytest.raises(DisclosureNotFoundError):
        await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=99)
    assert await repo.get_latest_consent(context.tenant_id, purpose) is None


async def test_negative_retired_disclosure_leaves_no_consent(consent_case):
    pool, repo, context, purpose = consent_case
    disclosure_id = await create_disclosure(pool, purpose=purpose)
    await retire_disclosure(pool, disclosure_id)
    with pytest.raises(DisclosureRetiredError):
        await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)
    assert await repo.get_latest_consent(context.tenant_id, purpose) is None


async def test_negative_duplicate_preserves_original_consent(consent_case):
    pool, repo, context, purpose = consent_case
    await create_disclosure(pool, purpose=purpose)
    await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)
    original = await repo.get_latest_consent(context.tenant_id, purpose)
    with pytest.raises(ConsentAlreadyActiveError):
        await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)
    assert await repo.get_latest_consent(context.tenant_id, purpose) == original
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM consent_record WHERE tenant_id=$1 AND purpose=$2",
            context.tenant_id, purpose,
        ) == 1


async def test_failure_injection_insert_denies_freshness_and_allows_retry(
    consent_case, monkeypatch,
):
    pool, repo, context, purpose = consent_case
    await create_disclosure(pool, purpose=purpose)
    failure = AsyncMock(side_effect=ConnectionError("injected consent storage failure"))
    with monkeypatch.context() as patch:
        patch.setattr(repo, "insert_consent", failure)
        with pytest.raises(ConnectionError, match="injected consent storage failure"):
            await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)
        failure.assert_awaited_once()
    assert await repo.get_latest_consent(context.tenant_id, purpose) is None
    denied = await evaluate_trust_freshness(repo, context, purpose=purpose)
    assert denied.is_fresh is False
    assert denied.reason_code == "POLICY_CONSENT_REQUIRED"
    await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)
    assert (await evaluate_trust_freshness(repo, context, purpose=purpose)).is_fresh is True


@pytest.mark.perf
async def test_consent_rejection_p95_under_borrowed_ack_budget(consent_case):
    """ADR-2026-09-09-C: borrow the paper ACK p95 50ms DB-path budget."""
    pool, repo, context, purpose = consent_case
    await create_disclosure(pool, purpose=purpose)
    samples = []
    for _ in range(30):
        started = perf_counter()
        with pytest.raises(DisclosureNotFoundError):
            await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=99)
        samples.append(perf_counter() - started)
    assert sorted(samples)[28] < 0.050
