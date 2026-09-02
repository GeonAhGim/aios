from __future__ import annotations

import time
from uuid import UUID

import asyncpg

from src.foundation.connections.application.begin_connection import (
    ACCOUNT_READ_CONSENT_PURPOSE,
)
from src.foundation.trust.application.accept_disclosure import accept_disclosure
from src.foundation.trust.contracts.v1 import TenantContext as TrustTenantContext
from src.foundation.trust.ports.repository import TrustRepository
from tests.foundation.integration.trust.conftest import create_disclosure

# tests/foundation/integration/mandates/test_mandate_lifecycle.py의
# `_next_material_change_disclosure_revision`과 동일한 이유 — 이 리프의 모든
# 테스트가 ACCOUNT_READ_CONSENT_PURPOSE라는 같은 앱 상수 purpose를 공유하므로,
# disclosure.revision을 매번 단조증가시켜 UNIQUE(purpose, revision) 충돌 없이,
# 그리고 "최신 동의" 판정이 항상 이 호출 쪽을 가리키게 한다. time.time()으로
# 시작하는 이유도 동일 — 1부터 시작하면 이전 pytest 프로세스 실행에서 DB에
# 남은(롤백되지 않는) disclosure 행과 재충돌한다.
_revision_counter = int(time.time())


def _next_revision() -> int:
    global _revision_counter
    _revision_counter += 1
    return _revision_counter


async def grant_account_read_consent(
    pool: asyncpg.Pool, trust_repo: TrustRepository, *, tenant_id: UUID
) -> None:
    """begin_connection()이 요구하는 ACCOUNT_READ_CONSENT_PURPOSE 동의를
    테스트용으로 미리 승인해둔다 — disclosure(purpose)가 없으면
    evaluate_trust_freshness가 POLICY_DISCLOSURE_NOT_PUBLISHED로 막는다."""
    revision = _next_revision()
    await create_disclosure(pool, purpose=ACCOUNT_READ_CONSENT_PURPOSE, revision=revision)
    await accept_disclosure(
        trust_repo,
        TrustTenantContext(tenant_id=tenant_id, subject_id=tenant_id, mfa_verified=True),
        purpose=ACCOUNT_READ_CONSENT_PURPOSE,
        disclosure_revision=revision,
    )
