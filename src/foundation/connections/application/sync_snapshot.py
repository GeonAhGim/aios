"""SyncSnapshot 커맨드 — provider에서 최신 스냅샷을 가져와 저장하고
connection_health를 갱신한다.

Spec: AIOSproject 74번 §2/§3/§5.

스콥 축소(명시): 74번 §2의 HealthCheck를 별도 actor/커맨드로 두지 않고 이
커맨드의 성공/실패 결과에 접합한다 — 이 코드베이스에 아직 백그라운드
스케줄러가 없어(마이그레이션 docstring 참조) 주기적 HealthCheck를 별도로
트리거할 대상이 없다. fetch 성공 = HEALTHY, 실패 = DEGRADED로 관측한다.

CON-004("concurrent revoke and sync cannot persist a post-revocation
snapshot") — 74번 §5 "workers re-read write state immediately before ...
persistence" 원칙대로, provider 호출 이후 스냅샷을 쓰기 직전에 connection
상태를 다시 읽어 REVOKED/DISCONNECTED면 그 결과를 버린다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.contracts.v1 import AccountSnapshotView
from src.foundation.connections.domain.models import (
    AccountSnapshot,
    ConnectionHealth,
    ConnectionState,
    HealthState,
)
from src.foundation.connections.ports.provider import OpaqueRef, ReadonlyAccountProvider
from src.foundation.connections.ports.repository import ConnectionRepository

_TERMINAL_STATES = frozenset({ConnectionState.REVOKED, ConnectionState.DISCONNECTED})
_SYNCABLE_STATES = frozenset({ConnectionState.ACTIVE_READONLY, ConnectionState.DEGRADED})


class ConnectionNotSyncableError(Exception):
    """ACTIVE_READONLY/DEGRADED가 아닌 connection(PENDING_CONSENT, REVOKED 등)은
    동기화 대상이 아니다."""


class ConnectionRevokedDuringSyncError(Exception):
    """CON-004 — provider 호출 도중 revoke가 먼저 커밋됐다. fetch 결과는
    버리고 저장하지 않는다."""


class ProviderUnavailableError(Exception):
    """CON-005 — provider timeout/rate-limit. DEGRADED로 관측만 하고, 원문
    provider 예외/에러 바디는 노출하지 않는다(72번 §4 에러 taxonomy)."""


def snapshot_to_view(snapshot: AccountSnapshot) -> AccountSnapshotView:
    return AccountSnapshotView(
        connection_id=snapshot.connection_id,
        captured_at=snapshot.captured_at,
        provider_as_of=snapshot.provider_as_of,
        freshness=snapshot.freshness,
        currency=snapshot.currency,
    )


async def sync_snapshot(
    repo: ConnectionRepository,
    provider: ReadonlyAccountProvider,
    *,
    tenant_id: UUID,
    connection_id: UUID,
) -> AccountSnapshotView:
    connection = await repo.get_connection(connection_id)
    if connection is None:
        raise ConnectionNotFoundError(str(connection_id))
    if connection.tenant_id != tenant_id:
        raise CrossTenantConnectionAccessError(str(connection_id))
    if connection.state not in _SYNCABLE_STATES:
        raise ConnectionNotSyncableError(
            f"{connection.state.value} 상태는 동기화 대상이 아닙니다."
        )

    now = datetime.now(timezone.utc)
    try:
        provider_snapshot = await provider.fetch_snapshot(
            OpaqueRef(connection.opaque_account_ref), now
        )
    except Exception as exc:
        await repo.insert_health_record(
            ConnectionHealth(
                connection_id=connection_id,
                evaluated_at=now,
                state=HealthState.DEGRADED,
                error_code="DEPENDENCY_PROVIDER_UNAVAILABLE",
                provider_trace_ref=type(exc).__name__,
            )
        )
        if connection.state == ConnectionState.ACTIVE_READONLY:
            await repo.transition_connection_state(
                connection_id, expected_state="ACTIVE_READONLY", new_state="DEGRADED"
            )
        raise ProviderUnavailableError("DEPENDENCY_PROVIDER_UNAVAILABLE") from exc

    # CON-004 재확인 — provider 호출은 시간이 걸리므로, 그 사이 revoke가
    # 먼저 커밋됐을 수 있다.
    fresh = await repo.get_connection(connection_id)
    if fresh is None or fresh.state in _TERMINAL_STATES:
        raise ConnectionRevokedDuringSyncError(str(connection_id))

    snapshot = await repo.insert_snapshot(
        AccountSnapshot(
            id=uuid4(),
            connection_id=connection_id,
            captured_at=now,
            provider_as_of=provider_snapshot.provider_as_of,
            freshness="PROVIDER_CONFIRMED",
            currency=provider_snapshot.currency,
            source_evidence_ref=provider_snapshot.raw_payload_ref,
        )
    )
    await repo.insert_health_record(
        ConnectionHealth(connection_id=connection_id, evaluated_at=now, state=HealthState.HEALTHY)
    )
    if fresh.state == ConnectionState.DEGRADED:
        await repo.transition_connection_state(
            connection_id, expected_state="DEGRADED", new_state="ACTIVE_READONLY"
        )
    return snapshot_to_view(snapshot)
