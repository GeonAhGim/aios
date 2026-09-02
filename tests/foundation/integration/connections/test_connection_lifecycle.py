"""FND-05 Connected Asset 통합테스트 — 실제 dev DB 대상. 74번 §6 CON-001~010 중
provider/real infra 없이 재현 가능한 범위."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.security.encryption import decrypt
from src.foundation.connections.adapters.fake_provider import FakeReadonlyAccountProvider
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.application.begin_connection import (
    ConsentRequiredError,
    MfaRequiredError,
    begin_connection,
)
from src.foundation.connections.application.confirm_connection import (
    confirm_connection,
)
from src.foundation.connections.application.errors import CrossTenantConnectionAccessError
from src.foundation.connections.application.revoke_connection import (
    ConnectionNotRevocableError,
    revoke_connection,
)
from src.foundation.connections.application.sync_snapshot import (
    ConnectionNotSyncableError,
    ConnectionRevokedDuringSyncError,
    ProviderUnavailableError,
    sync_snapshot,
)
from src.foundation.connections.domain.models import CapabilityScope, ProviderSnapshot
from src.foundation.connections.domain.rules import ForbiddenCapabilityScopeError
from src.foundation.connections.ports.provider import (
    OpaqueRef,
    ReadonlyAccountProvider,
    SecretLease,
)
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.connections.conftest import grant_account_read_consent
from tests.integration.conftest import create_test_user

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


async def _tenant(pool):
    return await create_test_user(pool)


async def _begin(pool, repo, trust_repo, tenant_id, *, scopes=None):
    await grant_account_read_consent(pool, trust_repo, tenant_id=tenant_id)
    return await begin_connection(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        mfa_verified=True,
        provider_code="fake-broker",
        opaque_account_ref="ACCT-1234567890",
        requested_capability_profile=scopes
        or ["READ_BALANCE", "READ_POSITION", "READ_ACTIVITY"],
    )


async def test_begin_connection_requires_mfa(pool, repo, trust_repo):
    tenant_id = await _tenant(pool)
    with pytest.raises(MfaRequiredError):
        await begin_connection(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            mfa_verified=False,
            provider_code="fake-broker",
            opaque_account_ref="ACCT-1",
            requested_capability_profile=["READ_BALANCE"],
        )


async def test_begin_connection_requires_active_consent(pool, repo, trust_repo):
    """CON-003 계열 — disclosure/consent가 아예 없으면 시작 자체가 거부된다."""
    tenant_id = await _tenant(pool)
    with pytest.raises(ConsentRequiredError):
        await begin_connection(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            mfa_verified=True,
            provider_code="fake-broker",
            opaque_account_ref="ACCT-1",
            requested_capability_profile=["READ_BALANCE"],
        )


async def test_full_lifecycle_begin_confirm_sync_revoke_masks_opaque_refs(pool, repo, trust_repo):
    """CON-001 — exact readonly scope activates connection and persists opaque
    refs only."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    assert created.masked_account_label == "***********7890"

    provider = FakeReadonlyAccountProvider()
    confirmed = await confirm_connection(
        repo,
        provider,
        tenant_id=tenant_id,
        connection_id=created.id,
        encryption_key=ENCRYPTION_KEY,
    )
    assert confirmed.state.value == "ACTIVE_READONLY"

    binding = await repo.get_credential_binding(created.id)
    assert binding is not None
    # vault_secret_ref는 암호문이지 원문 provider_credential_ref가 아니다.
    assert "fake-cred-" not in binding.vault_secret_ref
    assert decrypt(binding.vault_secret_ref, ENCRYPTION_KEY).startswith("fake-cred-")

    snapshot = await sync_snapshot(
        repo, provider, tenant_id=tenant_id, connection_id=created.id
    )
    assert snapshot.currency == "USD"

    revoked = await revoke_connection(repo, tenant_id=tenant_id, connection_id=created.id)
    assert revoked.state.value == "REVOKED"

    with pytest.raises(ConnectionNotRevocableError):
        await revoke_connection(repo, tenant_id=tenant_id, connection_id=created.id)


async def test_confirm_connection_rejects_scope_drift(pool, repo, trust_repo):
    """provider가 요청보다 더 넓은 scope를 승인하면(TRADE 등으로 드리프트)
    ACTIVE_READONLY로 전이하지 않는다."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id, scopes=["READ_BALANCE"])

    drifting_provider = FakeReadonlyAccountProvider(
        granted_scopes=(CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
    )
    with pytest.raises(ForbiddenCapabilityScopeError):
        await confirm_connection(
            repo,
            drifting_provider,
            tenant_id=tenant_id,
            connection_id=created.id,
            encryption_key=ENCRYPTION_KEY,
        )


async def test_sync_after_revoke_is_rejected(pool, repo, trust_repo):
    """CON-003 — revoked connection prevents lease, fetch, snapshot, and retry."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )
    await revoke_connection(repo, tenant_id=tenant_id, connection_id=created.id)

    with pytest.raises(ConnectionNotSyncableError):
        await sync_snapshot(repo, provider, tenant_id=tenant_id, connection_id=created.id)


async def test_concurrent_revoke_during_sync_discards_snapshot(pool, repo, trust_repo):
    """CON-004 — concurrent revoke and sync cannot persist a post-revocation
    snapshot. fetch_snapshot() 도중 revoke가 먼저 커밋되는 상황을
    재현한다(105번 §4 형태 B — 선행 변경을 직접 주입)."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )

    class RevokingProvider:
        async def verify_readonly_scope(self, lease: SecretLease):  # noqa: ANN201
            raise NotImplementedError

        async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime):
            # provider 호출이 진행되는 "동안" 다른 요청이 revoke를 먼저 커밋했다고
            # 가정한다.
            await revoke_connection(repo, tenant_id=tenant_id, connection_id=created.id)
            return ProviderSnapshot(
                provider_as_of=datetime.now(timezone.utc), currency="USD", raw_payload_ref="x"
            )

    revoking_provider: ReadonlyAccountProvider = RevokingProvider()
    with pytest.raises(ConnectionRevokedDuringSyncError):
        await sync_snapshot(
            repo, revoking_provider, tenant_id=tenant_id, connection_id=created.id
        )

    assert await repo.get_latest_snapshot(created.id) is None


async def test_provider_failure_degrades_connection_without_leaking_error_body(
    pool, repo, trust_repo
):
    """CON-005 — provider timeout/rate-limit yields DEGRADED, no credential/event leak."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )

    failing_provider = FakeReadonlyAccountProvider(fail_fetch=True)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await sync_snapshot(
            repo, failing_provider, tenant_id=tenant_id, connection_id=created.id
        )
    assert "시뮬레이션" not in str(excinfo.value)

    connection = await repo.get_connection(created.id)
    assert connection is not None
    assert connection.state.value == "DEGRADED"

    health = await repo.get_latest_health(created.id)
    assert health is not None
    assert health.state.value == "DEGRADED"
    assert health.provider_trace_ref == "ConnectionError"


async def test_confirm_connection_cross_tenant_is_rejected(pool, repo, trust_repo):
    """다른 tenant의 connection은 confirm 대상이 될 수 없다 — 라우터가 이
    예외와 ConnectionNotFoundError를 똑같이 404로 매핑한다(cross-tenant
    존재 여부 자체를 흘리지 않기 위함, adversarial 테스트에서 상세 검증)."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_a)
    provider = FakeReadonlyAccountProvider()

    with pytest.raises(CrossTenantConnectionAccessError):
        await confirm_connection(
            repo,
            provider,
            tenant_id=tenant_b,
            connection_id=created.id,
            encryption_key=ENCRYPTION_KEY,
        )


def test_provider_protocol_has_no_trade_transfer_sign_method():
    """CON-009 — adapter has no callable order/transfer/sign method by
    contract test."""
    forbidden_substrings = ("order", "trade", "transfer", "withdraw", "sign")
    methods = [name for name in dir(ReadonlyAccountProvider) if not name.startswith("_")]
    for method in methods:
        lowered = method.lower()
        assert not any(f in lowered for f in forbidden_substrings), method
