"""Trust Core adversarial 테스트 — 71번 §3 FND-01 negative test: cross-tenant.

Spec: AIOSproject 73번 §9 TRU-006 "cross-tenant ID in route/body/query cannot
read or mutate record".
"""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.accept_disclosure import accept_disclosure
from src.foundation.trust.application.revoke_consent import (
    CrossTenantConsentAccessError,
    revoke_consent,
)
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.projections import build_trust_status_view
from tests.foundation.integration.trust.conftest import create_disclosure, unique_purpose
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


async def _context_for(pool) -> TenantContext:
    user_id = await create_test_user(pool)
    return TenantContext(tenant_id=user_id, subject_id=user_id, role="OWNER", mfa_verified=False)


async def test_cannot_revoke_another_tenants_consent(pool, repo):
    purpose = unique_purpose()
    owner_context = await _context_for(pool)
    attacker_context = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    owned = await accept_disclosure(
        repo, owner_context, purpose=purpose, disclosure_revision=1
    )

    with pytest.raises(CrossTenantConsentAccessError):
        await revoke_consent(repo, attacker_context, consent_id=owned.consent_id)

    # 공격 시도 이후에도 원 소유자의 동의는 그대로 ACTIVE여야 한다.
    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM consent_record WHERE id = $1", owned.consent_id
        )
    assert state == "ACTIVE"


async def test_revoking_nonexistent_consent_raises_lookup_error(pool, repo):
    context = await _context_for(pool)

    with pytest.raises(LookupError):
        await revoke_consent(repo, context, consent_id=uuid4())


async def test_tenant_status_view_never_includes_another_tenants_consent(pool, repo):
    purpose = unique_purpose()
    tenant_a = await _context_for(pool)
    tenant_b = await _context_for(pool)
    await create_disclosure(pool, purpose=purpose, revision=1)
    await accept_disclosure(repo, tenant_a, purpose=purpose, disclosure_revision=1)

    view_b = await build_trust_status_view(repo, tenant_b.tenant_id)

    assert view_b.consents == []
