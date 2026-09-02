"""Trust Core 통합테스트 — 실제 dev DB 대상. 71번 §7 "정상 흐름 + negative test"."""
import asyncio
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.accept_disclosure import (
    ConsentAlreadyActiveError,
    DisclosureNotFoundError,
    DisclosureRetiredError,
    accept_disclosure,
)
from src.foundation.trust.application.evaluate_trust_freshness import evaluate_trust_freshness
from src.foundation.trust.application.revoke_consent import revoke_consent
from src.foundation.trust.contracts.v1 import ConsentState, TenantContext
from tests.foundation.integration.trust.conftest import (
    create_disclosure,
    retire_disclosure,
    unique_purpose,
)
from tests.integration.conftest import create_test_user


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


async def _context_for(pool) -> TenantContext:
    user_id = await create_test_user(pool)
    return TenantContext(tenant_id=user_id, subject_id=user_id, role="OWNER", mfa_verified=False)


async def test_accept_disclosure_creates_active_consent(pool, repo, purpose):
    context = await _context_for(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)

    result = await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)

    assert result.state == ConsentState.ACTIVE
    assert result.disclosure_id == disclosure_id
    assert result.tenant_id == context.tenant_id


async def test_accept_disclosure_for_unknown_revision_raises(pool, repo, purpose):
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)

    with pytest.raises(DisclosureNotFoundError):
        await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=99)


async def test_accept_retired_disclosure_raises(pool, repo, purpose):
    context = await _context_for(pool)
    disclosure_id = await create_disclosure(pool, purpose=purpose, revision=1)
    await retire_disclosure(pool, disclosure_id)

    with pytest.raises(DisclosureRetiredError):
        await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)


async def test_accepting_same_revision_twice_raises_already_active(pool, repo, purpose):
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)

    with pytest.raises(ConsentAlreadyActiveError):
        await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)


async def test_concurrent_accept_disclosure_only_one_succeeds(pool, repo, purpose):
    """105번 §4 형태 A — uq_consent_record_active_purpose 부분 unique index가
    실제로 경합을 막는지 asyncio.gather로 재현."""
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)

    async def attempt():
        return await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [
        r for r in results if isinstance(r, (ConcurrencyConflictError, ConsentAlreadyActiveError))
    ]
    assert len(successes) == 1
    assert len(failures) == 1


async def test_evaluate_trust_freshness_is_fresh_after_accept(pool, repo, purpose):
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)

    decision = await evaluate_trust_freshness(repo, context, purpose=purpose)

    assert decision.is_fresh is True
    assert decision.reason_code is None


async def test_evaluate_trust_freshness_without_consent_is_not_fresh(pool, repo, purpose):
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)

    decision = await evaluate_trust_freshness(repo, context, purpose=purpose)

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_REQUIRED"


async def test_revoke_consent_makes_freshness_denied(pool, repo, purpose):
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    accepted = await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)

    revoked = await revoke_consent(repo, context, consent_id=accepted.consent_id)
    assert revoked.state == ConsentState.REVOKED

    decision = await evaluate_trust_freshness(repo, context, purpose=purpose)
    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_REVOKED"


async def test_revoking_already_revoked_consent_raises_concurrency_conflict(pool, repo, purpose):
    """105번 §2 — revoke는 조건부 UPDATE(state=ACTIVE)라 이미 REVOKED인 걸 다시
    revoke하면 실패한다(같은 코드를 재요청하는 시나리오)."""
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    accepted = await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)
    await revoke_consent(repo, context, consent_id=accepted.consent_id)

    with pytest.raises(ConcurrencyConflictError):
        await revoke_consent(repo, context, consent_id=accepted.consent_id)


async def test_new_disclosure_revision_requires_new_consent(pool, repo, purpose):
    context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    await create_disclosure(pool, purpose=purpose, revision=2)
    await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=1)

    decision = await evaluate_trust_freshness(repo, context, purpose=purpose)

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_STALE_REVISION"

    accepted_v2 = await accept_disclosure(repo, context, purpose=purpose, disclosure_revision=2)
    assert accepted_v2.disclosure_revision == 2

    decision_after = await evaluate_trust_freshness(repo, context, purpose=purpose)
    assert decision_after.is_fresh is True
