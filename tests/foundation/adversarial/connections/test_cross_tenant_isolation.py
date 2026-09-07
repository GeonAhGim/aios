"""Connected Asset adversarial 테스트 — 74번 §6 CON-007 "tenant A cannot
access, label, revoke, or reference tenant B connection"."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.connections.adapters.fake_provider import FakeReadonlyAccountProvider
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.application.begin_connection import begin_connection
from src.foundation.connections.application.confirm_connection import confirm_connection
from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.application.revoke_connection import revoke_connection
from src.foundation.connections.application.sync_snapshot import sync_snapshot
from src.foundation.connections.domain.models import (
    AccountSnapshot,
    ConnectionHealth,
    HealthState,
)
from src.foundation.connections.projections import build_connection_list_view
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.connections.conftest import grant_account_read_consent
from tests.integration.conftest import create_test_tenant

ENCRYPTION_KEY = "22" * 32


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
    return PostgresConnectionRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


async def _owned_connection(pool, repo, trust_repo, owner_id):
    await grant_account_read_consent(pool, trust_repo, tenant_id=owner_id)
    return await begin_connection(
        repo,
        trust_repo,
        tenant_id=owner_id,
        subject_id=owner_id,
        mfa_verified=True,
        provider_code="fake-broker",
        opaque_account_ref="ACCT-owner-1",
        # FakeReadonlyAccountProvider() 기본값(READ_BALANCE/POSITION/ACTIVITY 전부
        # 승인)과 정확히 일치시켜야 confirm_connection()의 scope drift 검사를
        # 통과한다 — 이 테스트의 관심사는 scope drift가 아니라 tenant 격리다.
        requested_capability_profile=["READ_BALANCE", "READ_POSITION", "READ_ACTIVITY"],
    )


async def test_cannot_confirm_another_tenants_connection(pool, repo, trust_repo):
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    owned = await _owned_connection(pool, repo, trust_repo, owner_id)

    provider = FakeReadonlyAccountProvider()
    with pytest.raises(CrossTenantConnectionAccessError):
        await confirm_connection(
            repo,
            provider,
            tenant_id=attacker_id,
            connection_id=owned.id,
            encryption_key=ENCRYPTION_KEY,
        )

    still_pending = await repo.get_connection(owned.id)
    assert still_pending.state.value == "PENDING_CONSENT"


async def test_cannot_revoke_another_tenants_connection(pool, repo, trust_repo):
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    owned = await _owned_connection(pool, repo, trust_repo, owner_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=owner_id, connection_id=owned.id, encryption_key=ENCRYPTION_KEY
    )

    with pytest.raises(CrossTenantConnectionAccessError):
        await revoke_connection(repo, tenant_id=attacker_id, connection_id=owned.id)

    still_active = await repo.get_connection(owned.id)
    assert still_active.state.value == "ACTIVE_READONLY"


async def test_cannot_sync_another_tenants_connection(pool, repo, trust_repo):
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    owned = await _owned_connection(pool, repo, trust_repo, owner_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=owner_id, connection_id=owned.id, encryption_key=ENCRYPTION_KEY
    )

    with pytest.raises(CrossTenantConnectionAccessError):
        await sync_snapshot(repo, provider, tenant_id=attacker_id, connection_id=owned.id)

    assert await repo.get_latest_snapshot(owned.id) is None


async def test_nonexistent_connection_raises_not_found_not_cross_tenant(pool, repo, trust_repo):
    """존재하지 않는 connection과 "다른 tenant 소유" connection은 라우터
    레벨에서 둘 다 404로 통일되지만, 서비스 레벨에서는 구분된다(존재 자체를
    흘리지 않는 것과, 아예 없는 것을 섞지 않기 위함)."""
    tenant_id = await create_test_tenant(pool)
    provider = FakeReadonlyAccountProvider()
    with pytest.raises(ConnectionNotFoundError):
        await confirm_connection(
            repo,
            provider,
            tenant_id=tenant_id,
            connection_id=uuid4(),
            encryption_key=ENCRYPTION_KEY,
        )


async def test_connection_list_view_never_includes_another_tenants_connection(
    pool, repo, trust_repo
):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _owned_connection(pool, repo, trust_repo, tenant_a)

    view_b = await build_connection_list_view(repo, tenant_b)

    assert view_b.connections == []


async def test_transition_connection_state_rejects_mismatched_tenant_at_repo_layer(
    pool, repo, trust_repo
):
    """task-1718 P0-E — 애플리케이션 계층의 tenant 검사를 우회해 리포지토리
    공개 메서드를 직접(attacker의 tenant_id로) 호출해도, 그 자체가
    독립적으로 거부돼야 한다(테스트 전용 헬퍼가 아니라 운영 경로 증명)."""
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    owned = await _owned_connection(pool, repo, trust_repo, owner_id)

    with pytest.raises(ConcurrencyConflictError):
        await repo.transition_connection_state(
            owned.id,
            tenant_id=attacker_id,
            expected_state="PENDING_CONSENT",
            new_state="CONNECTING",
        )

    still_pending = await repo.get_connection(owned.id)
    assert still_pending.state.value == "PENDING_CONSENT"


async def test_persist_snapshot_if_syncable_rejects_mismatched_tenant_at_repo_layer(
    pool, repo, trust_repo
):
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    owned = await _owned_connection(pool, repo, trust_repo, owner_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=owner_id, connection_id=owned.id, encryption_key=ENCRYPTION_KEY
    )
    now = datetime.now(timezone.utc)

    with pytest.raises(ConcurrencyConflictError):
        await repo.persist_snapshot_if_syncable(
            owned.id,
            attacker_id,
            AccountSnapshot(
                id=uuid4(),
                connection_id=owned.id,
                captured_at=now,
                provider_as_of=now,
                freshness="PROVIDER_CONFIRMED",
                currency="USD",
                source_evidence_ref="attacker-attempt",
            ),
            ConnectionHealth(connection_id=owned.id, evaluated_at=now, state=HealthState.HEALTHY),
        )

    assert await repo.get_latest_snapshot(owned.id) is None


async def test_transition_connection_state_requires_explicit_tenant_context(pool, repo):
    """PLT-30 DoD — 컨텍스트 없는 접근은 무음 0행이 아니라 예외여야 한다.
    `tenant_id`가 키워드 전용 필수 인자라, 호출부가 tenant 컨텍스트 없이
    호출하는 것 자체가 (조용히 통과하는 대신) 즉시 TypeError로 막힌다."""
    with pytest.raises(TypeError):
        await repo.transition_connection_state(
            uuid4(), expected_state="PENDING_CONSENT", new_state="CONNECTING"
        )
