"""SyncSnapshot command — fetches the latest snapshot from the provider, persists it,
and updates connection_health.

Spec: AIOSproject #74 §2/§3/§5.

Scope reduction (explicit): We do not give HealthCheck from #74 §2 its own actor/command;
instead we fold it into the success/failure outcome of this command — the codebase has no
background scheduler yet (see migration docstring), so there is no target to periodically
trigger a standalone HealthCheck. fetch success → HEALTHY, failure → DEGRADED.

CON-004 ("concurrent revoke and sync cannot persist a post-revocation snapshot") — After
calling the provider we re-confirm connection state and persist snapshot/health in a single
transaction + row lock via
`ConnectionRepository.persist_snapshot_if_syncable()` (#74 §5: "workers re-read write state
immediately before ... persistence"). We discovered during review that separating re-confirmation
and persistence into two DB round-trips leaves a TOCTOU gap, so we fixed it — see
adapters/postgres_repository.py for details.
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
    """Connections not in ACTIVE_READONLY/DEGRADED (PENDING_CONSENT, REVOKED, etc.)
    are not sync targets."""


class ConnectionRevokedDuringSyncError(Exception):
    """CON-004 — A revoke committed before our provider call finished. Discard fetch
    results; do not persist."""


class ProviderUnavailableError(Exception):
    """CON-005 — Provider timeout/rate-limit. Observe as DEGRADED only; do not expose
    raw provider exception/error body (#72 §4 error taxonomy)."""


class MalformedProviderResponseError(Exception):
    """CON-006 — Provider reported a future provider_as_of (clock skew or tampering).
    Do not persist."""


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
                connection_id,
                tenant_id=tenant_id,
                expected_state="ACTIVE_READONLY",
                new_state="DEGRADED",
            )
        raise ProviderUnavailableError("DEPENDENCY_PROVIDER_UNAVAILABLE") from exc

    # CON-006 — Classify whether this response is future-dated (tampering/clock skew)
    # or older than what we already know (late arrival / duplicate retransmission).
    # This judgment is independent of fetch success/failure — the provider call already
    # succeeded, so even STALE is observed as HEALTHY (response received normally), but
    # we do not overwrite history with the new snapshot. "Now" is re-measured after the
    # provider call finishes — if we reused the `now` captured before the call, even a
    # short provider round-trip could make a normal response look future-dated vs. our
    # stale `now`, causing a false FUTURE_DATED classification (discovered during review).
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
        assert latest is not None  # STALE is only returned when latest exists (rules.py)
        return snapshot_to_view(latest)

    # CON-004 — provider calls take time, so a revoke may have committed in between.
    # Two separate round-trips for re-confirmation + persistence leave a TOCTOU gap —
    # persist_snapshot_if_syncable() closes the gap by combining re-confirmation +
    # persistence in a single transaction (see adapter docstring).
    try:
        snapshot = await repo.persist_snapshot_if_syncable(
            connection_id,
            tenant_id,
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
