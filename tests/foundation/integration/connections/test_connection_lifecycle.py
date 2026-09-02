"""FND-05 Connected Asset 통합테스트 — 실제 dev DB 대상. 74번 §6 CON-001~010 중
provider/real infra 없이 재현 가능한 범위."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.security.encryption import legacy_decrypt
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
    MalformedProviderResponseError,
    ProviderUnavailableError,
    sync_snapshot,
)
from src.foundation.connections.domain.models import (
    CapabilityScope,
    ProviderSnapshot,
    SnapshotValue,
)
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
    assert legacy_decrypt(binding.vault_secret_ref, ENCRYPTION_KEY).startswith("fake-cred-")

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


async def test_real_concurrent_revoke_and_sync_never_leaves_post_revocation_snapshot(
    pool, repo, trust_repo
):
    """CON-004 회귀 — 리뷰 중 발견한 진짜 TOCTOU를 재현한다. 이전 버전은 상태
    재확인(get_connection)과 저장(insert_snapshot)이 별도 DB 왕복 두 번이라
    그 사이에 revoke가 커밋될 수 있는 틈이 있었다. 이번엔 인위적으로 순서를
    강제하지 않고(위 test_concurrent_revoke_during_sync_discards_snapshot처럼
    fetch_snapshot 안에서 revoke를 호출하는 방식이 아니라) 진짜
    asyncio.gather로 동시에 실행한다 — persist_snapshot_if_syncable()의
    SELECT ... FOR UPDATE 행 잠금이 revoke_connection()의
    transition_connection_state() UPDATE와 실제로 직렬화되는지 확인한다."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )

    class _SlowProvider:
        async def verify_readonly_scope(self, lease: SecretLease):  # noqa: ANN201
            raise NotImplementedError

        async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime):
            # provider 호출 자체에 약간의 지연을 둬서, revoke_connection()이
            # persist_snapshot_if_syncable()의 SELECT ... FOR UPDATE보다 먼저
            # 도착할 확률을 높인다 — 어느 쪽이 이기든 결과가 일관되어야 한다.
            await asyncio.sleep(0.05)
            return ProviderSnapshot(
                provider_as_of=datetime.now(timezone.utc), currency="USD", raw_payload_ref="x"
            )

    slow_provider: ReadonlyAccountProvider = _SlowProvider()

    sync_result, revoke_result = await asyncio.gather(
        sync_snapshot(repo, slow_provider, tenant_id=tenant_id, connection_id=created.id),
        revoke_connection(repo, tenant_id=tenant_id, connection_id=created.id),
        return_exceptions=True,
    )

    final = await repo.get_connection(created.id)
    assert final.state.value == "REVOKED"

    snapshot_persisted = not isinstance(sync_result, Exception)
    if not snapshot_persisted:
        assert isinstance(sync_result, ConnectionRevokedDuringSyncError)
    assert not isinstance(revoke_result, Exception)
    # 핵심 불변조건 — sync가 성공했다면 그건 revoke의 실제 커밋보다 먼저
    # 일어났다는 뜻이고(row lock이 강제하는 직렬 순서), 실패했다면 저장된
    # 스냅샷이 전혀 없어야 한다. 둘 다 "REVOKED 이후 스냅샷"은 없다.
    if not snapshot_persisted:
        assert await repo.get_latest_snapshot(created.id) is None


class _FixedAsOfProvider:
    """CON-006 테스트 전용 — provider_as_of를 원하는 값으로 고정한다.
    `FakeReadonlyAccountProvider`는 항상 `now()`를 반환해 stale/future 응답을
    재현할 수 없다."""

    def __init__(
        self,
        provider_as_of: datetime,
        *,
        raw_payload_ref: str = "x",
        values: tuple[SnapshotValue, ...] = (),
    ) -> None:
        self._provider_as_of = provider_as_of
        self._raw_payload_ref = raw_payload_ref
        self._values = values

    async def verify_readonly_scope(self, lease: SecretLease):  # noqa: ANN201
        raise NotImplementedError

    async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider_as_of=self._provider_as_of,
            currency="USD",
            raw_payload_ref=self._raw_payload_ref,
            values=self._values,
        )


async def test_stale_provider_response_does_not_overwrite_latest_snapshot(
    pool, repo, trust_repo
):
    """CON-006 — 지연 도착·재전송된 오래된 응답은 저장된 "최신"을 덮어쓰지
    않는다. sync 자체는 실패로 취급하지 않는다(provider 호출은 정상적으로
    성공했으므로) — 기존 최신 스냅샷을 그대로 반환한다."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )

    now = datetime.now(timezone.utc)
    fresh_provider: ReadonlyAccountProvider = _FixedAsOfProvider(now)
    first = await sync_snapshot(
        repo, fresh_provider, tenant_id=tenant_id, connection_id=created.id
    )

    stale_provider: ReadonlyAccountProvider = _FixedAsOfProvider(now - timedelta(hours=1))
    second = await sync_snapshot(
        repo, stale_provider, tenant_id=tenant_id, connection_id=created.id
    )

    assert second.provider_as_of == first.provider_as_of  # 최신이 여전히 첫 번째 응답
    latest = await repo.get_latest_snapshot(created.id)
    assert latest is not None and latest.provider_as_of == first.provider_as_of

    # DB에도 오래된 응답이 새 행으로 남지 않았어야 한다 — 이력을 덮어쓰지도,
    # 오염시키지도 않는다.
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM account_snapshot WHERE connection_id = $1", created.id
        )
    assert count == 1


async def test_future_dated_provider_response_is_rejected(pool, repo, trust_repo):
    """CON-006 — provider가 미래 시각을 보고하면(시계 오류·변조 가능성)
    저장을 거부하고 DEGRADED로 관측한다."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider()
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )

    future_provider: ReadonlyAccountProvider = _FixedAsOfProvider(
        datetime.now(timezone.utc) + timedelta(days=1)
    )
    with pytest.raises(MalformedProviderResponseError):
        await sync_snapshot(repo, future_provider, tenant_id=tenant_id, connection_id=created.id)

    assert await repo.get_latest_snapshot(created.id) is None
    health = await repo.get_latest_health(created.id)
    assert health is not None
    assert health.error_code == "INTEGRITY_FUTURE_DATA"


async def test_confirmed_connection_view_reflects_scope_verified(pool, repo, trust_repo):
    """전수감사 §6 — FakeReadonlyAccountProvider(시뮬레이션)는 scope_verified=
    True, credential_binding에도 그대로 영속화된다."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    assert created.scope_verified is False  # 아직 confirm 전

    confirmed = await confirm_connection(
        repo,
        FakeReadonlyAccountProvider(),
        tenant_id=tenant_id,
        connection_id=created.id,
        encryption_key=ENCRYPTION_KEY,
    )
    assert confirmed.scope_verified is True

    binding = await repo.get_credential_binding(created.id)
    assert binding.scope_verified is True


async def test_snapshot_values_persist_and_round_trip(pool, repo, trust_repo):
    """전수감사 §6 — provider가 실제 잔고 수치를 돌려주면 account_snapshot_
    value에 저장되고 get_latest_snapshot()으로 그대로 되돌아온다."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    provider = FakeReadonlyAccountProvider(
        snapshot_values=(
            SnapshotValue(entity_type="BALANCE", entity_key="USDT", value=Decimal("1234.5")),
            SnapshotValue(entity_type="BALANCE", entity_key="BTC", value=Decimal("0.01")),
        )
    )
    await confirm_connection(
        repo, provider, tenant_id=tenant_id, connection_id=created.id, encryption_key=ENCRYPTION_KEY
    )

    await sync_snapshot(repo, provider, tenant_id=tenant_id, connection_id=created.id)

    latest = await repo.get_latest_snapshot(created.id)
    assert latest is not None
    values_by_key = {v.entity_key: v.value for v in latest.values}
    assert values_by_key == {"USDT": Decimal("1234.5000000000"), "BTC": Decimal("0.0100000000")}


async def test_duplicate_sync_does_not_duplicate_snapshot_values(pool, repo, trust_repo):
    """REC-004/006과 같은 원칙 — 같은 provider_as_of/증적으로 중복 sync가
    와도(재시도) 값 행이 두 번 쌓이지 않는다(ON CONFLICT DO NOTHING 재조회
    경로에서는 값을 다시 쓰지 않는다)."""
    tenant_id = await _tenant(pool)
    created = await _begin(pool, repo, trust_repo, tenant_id)
    await confirm_connection(
        repo,
        FakeReadonlyAccountProvider(),
        tenant_id=tenant_id,
        connection_id=created.id,
        encryption_key=ENCRYPTION_KEY,
    )

    fixed_as_of = datetime.now(timezone.utc)
    provider = _FixedAsOfProvider(
        fixed_as_of,
        raw_payload_ref="dup-test",
        values=(SnapshotValue(entity_type="BALANCE", entity_key="USDT", value=Decimal("10")),),
    )
    first = await sync_snapshot(repo, provider, tenant_id=tenant_id, connection_id=created.id)
    second = await sync_snapshot(repo, provider, tenant_id=tenant_id, connection_id=created.id)
    assert first.provider_as_of == second.provider_as_of

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM account_snapshot_value av "
            "JOIN account_snapshot s ON s.id = av.snapshot_id WHERE s.connection_id = $1",
            created.id,
        )
    assert count == 1
