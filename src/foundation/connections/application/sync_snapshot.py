"""SyncSnapshot 커맨드 — provider에서 최신 스냅샷을 가져와 저장하고
connection_health를 갱신한다.

Spec: AIOSproject 74번 §2/§3/§5.

스콥 축소(명시): 74번 §2의 HealthCheck를 별도 actor/커맨드로 두지 않고 이
커맨드의 성공/실패 결과에 접합한다 — 이 코드베이스에 아직 백그라운드
스케줄러가 없어(마이그레이션 docstring 참조) 주기적 HealthCheck를 별도로
트리거할 대상이 없다. fetch 성공 = HEALTHY, 실패 = DEGRADED로 관측한다.

CON-004("concurrent revoke and sync cannot persist a post-revocation
snapshot") — provider 호출 이후 connection 상태 재확인과 snapshot/health
저장을 `ConnectionRepository.persist_snapshot_if_syncable()` 하나의 트랜잭션
+ row lock으로 묶어 처리한다(74번 §5 "workers re-read write state
immediately before ... persistence"). 재확인과 저장이 별도 두 번의 DB
왕복이면 그 사이에 TOCTOU 틈이 남는다는 걸 리뷰 중 발견해 고쳤다 — 자세한
내용은 adapters/postgres_repository.py 참조.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
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
from src.foundation.connections.domain.rules import (
    ProviderResponseClassification,
    classify_provider_response,
)
from src.foundation.connections.ports.provider import OpaqueRef, ReadonlyAccountProvider
from src.foundation.connections.ports.repository import ConnectionRepository

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


class MalformedProviderResponseError(Exception):
    """CON-006 — provider가 미래 시각의 provider_as_of를 보고했다(시계 오류
    또는 변조 가능성). 저장하지 않는다."""


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

    # CON-006 — 저장하기 전에 이 응답이 미래 시각이거나(변조/시계 오류) 이미
    # 아는 것보다 과거인지(지연 도착·중복 재전송) 분류한다. 이 판정은 fetch
    # 자체의 성공/실패와 별개다 — provider 호출은 이미 성공했으므로 STALE도
    # HEALTHY로 관측하되(정상적으로 응답은 받음), 새 스냅샷으로 이력을
    # 덮어쓰지는 않는다. "지금"은 provider 호출이 끝난 뒤 다시 잰다 —
    # 위 `now`(호출 전에 잰 시각)를 그대로 쓰면, provider 호출 자체가 조금만
    # 걸려도 provider의 실제 현재 시각이 우리 쪽 `now`보다 미래처럼 보여
    # 정상 응답을 FUTURE_DATED로 오판하게 된다(리뷰 중 발견).
    classification_now = datetime.now(timezone.utc)
    latest = await repo.get_latest_snapshot(connection_id)
    classification = classify_provider_response(
        provider_as_of=provider_snapshot.provider_as_of,
        latest_known_as_of=latest.provider_as_of if latest is not None else None,
        now=classification_now,
    )
    if classification == ProviderResponseClassification.FUTURE_DATED:
        await repo.insert_health_record(
            ConnectionHealth(
                connection_id=connection_id,
                evaluated_at=now,
                state=HealthState.DEGRADED,
                error_code="INTEGRITY_FUTURE_DATA",
                provider_trace_ref=None,
            )
        )
        raise MalformedProviderResponseError("INTEGRITY_FUTURE_DATA")
    if classification == ProviderResponseClassification.STALE:
        await repo.insert_health_record(
            ConnectionHealth(
                connection_id=connection_id, evaluated_at=now, state=HealthState.HEALTHY
            )
        )
        assert latest is not None  # STALE은 latest가 있을 때만 나온다(rules.py)
        return snapshot_to_view(latest)

    # CON-004 — provider 호출은 시간이 걸리므로, 그 사이 revoke가 먼저
    # 커밋됐을 수 있다. 재확인과 저장을 별도 왕복 두 번으로 하면 그 사이에도
    # TOCTOU 틈이 남는다 — persist_snapshot_if_syncable()이 재확인+저장을
    # 트랜잭션 하나로 묶어 그 틈을 없앤다(어댑터 docstring 참조).
    try:
        snapshot = await repo.persist_snapshot_if_syncable(
            connection_id,
            AccountSnapshot(
                id=uuid4(),
                connection_id=connection_id,
                captured_at=now,
                provider_as_of=provider_snapshot.provider_as_of,
                freshness="PROVIDER_CONFIRMED",
                currency=provider_snapshot.currency,
                source_evidence_ref=provider_snapshot.raw_payload_ref,
                values=provider_snapshot.values,
            ),
            ConnectionHealth(
                connection_id=connection_id, evaluated_at=now, state=HealthState.HEALTHY
            ),
        )
    except ConcurrencyConflictError as exc:
        raise ConnectionRevokedDuringSyncError(str(connection_id)) from exc
    return snapshot_to_view(snapshot)
