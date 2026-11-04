from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.application.begin_connection import (
    ACCOUNT_READ_CONSENT_PURPOSE,
)
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.accept_disclosure import accept_disclosure
from src.foundation.trust.contracts.v1 import TenantContext as TrustTenantContext
from src.foundation.trust.ports.repository import TrustRepository
from tests.foundation.integration.trust.conftest import create_disclosure


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
def trust_repo(pool: asyncpg.Pool) -> PostgresTrustRepository:
    return PostgresTrustRepository(pool)


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresConnectionRepository:
    return PostgresConnectionRepository(pool)


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


# ---------------------------------------------------------------------------
# Negative / failure-injection tests for conftest helpers (DoD 보강)
# ---------------------------------------------------------------------------


def test_next_revision_is_strictly_monotonic() -> None:
    """_next_revision()은 호출할 때마다 단조증가해야 한다.
    중복 revision으로 UNIQUE(purpose, revision) 충돌이 나면
    begin_connection이 조용히 실패하므로 단조증가는 불변식이다."""
    first = _next_revision()
    for _ in range(10):
        assert _next_revision() > first


async def test_grant_account_read_consent_rejects_closed_pool(
    pool: asyncpg.Pool,
    trust_repo: TrustRepository,
) -> None:
    """닫힌 pool로 grant_account_read_consent()를 호출하면
    OperationalError(또는 하위 예외)가 던져져야 한다 — DB 연결이
    끊겼을 때 테스트가 조용히 통과하면 안 된다."""
    await pool.close()
    # pool이 닫힌 상태에서 disclosure 삽입은 예외를 던져야 한다.
    try:
        await create_disclosure(pool, purpose="closed-pool-test", revision=99999)
    except asyncpg.PostgresError as exc:
        # asyncpg는 연결이 닫히면 OperationalError를 던진다.
        assert (
            "pool" in str(exc).lower()
            or "closed" in str(exc).lower()
            or "operational" in type(exc).__name__.lower()
        ), f"Expected pool-closed error, got: {exc}"
    else:
        pytest.fail("create_disclosure with closed pool must raise")
