"""Shared PauseDeployment/StopDeployment — both increment the fence token to
invalidate in-flight (or future) ticks/intents.

Spec: AIOSproject §3 "Pause: fence token increments, cancel future
ticks/intents" / "Stop: terminal stop, cancel work". "STOP and risk/emergency
PAUSE take precedence over START/RESUME" (§2) is implemented only up to the
point where idempotency_key uniqueness guarantees ordering, since this
codebase lacks a true concurrent command scheduler (full "concurrent start/stop"
reproduction in PAP-003 is verified via 105 §4 Form A tests)."""

from __future__ import annotations

import asyncio
from uuid import UUID

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.paper_control.application.request_deployment import deployment_to_view
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.domain.models import (
    CommandOutcome,
    CommandType,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository

_STOPPABLE_STATES = frozenset(
    {
        DeploymentState.READY,
        DeploymentState.RUNNING,
        DeploymentState.PAUSED,
        DeploymentState.DEGRADED,
        DeploymentState.RECOVERY_REVIEW,
    }
)


class DeploymentNotFoundError(Exception):
    pass


class CrossTenantDeploymentAccessError(Exception):
    pass


class InvalidDeploymentStateError(Exception):
    pass


async def _idempotent_or_none(
    repo: PaperControlRepository, deployment_id: UUID, idempotency_key: str
) -> PaperDeploymentView | None:
    existing = await repo.get_command_by_idempotency_key(deployment_id, idempotency_key)
    if existing is None:
        return None
    current = await repo.get_deployment(deployment_id)
    assert current is not None
    return deployment_to_view(current)


_IDEMPOTENCY_LOOKUP_ATTEMPTS = 10
_IDEMPOTENCY_LOOKUP_DELAY_SEC = 0.005


async def _idempotent_or_none_awaiting_concurrent_winner(
    repo: PaperControlRepository, deployment_id: UUID, idempotency_key: str
) -> PaperDeploymentView | None:
    """`increment_fence()` and `insert_command()` are two sequential writes, not one
    atomic transaction, so a concurrent same-`idempotency_key` request can observe the
    deployment already in the post-pause state before the winner's command row is
    visible yet. A short bounded retry closes that microsecond-scale window instead of
    surfacing a spurious conflict to a caller that is simply retrying its own request
    (task-10780 A6-G5-4)."""
    for attempt in range(_IDEMPOTENCY_LOOKUP_ATTEMPTS):
        cached = await _idempotent_or_none(repo, deployment_id, idempotency_key)
        if cached is not None:
            return cached
        if attempt < _IDEMPOTENCY_LOOKUP_ATTEMPTS - 1:
            await asyncio.sleep(_IDEMPOTENCY_LOOKUP_DELAY_SEC)
    return None


async def _load_owned_deployment(
    repo: PaperControlRepository, *, tenant_id: UUID, deployment_id: UUID
) -> PaperDeployment:
    deployment = await repo.get_deployment(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError(str(deployment_id))
    if deployment.tenant_id != tenant_id:
        raise CrossTenantDeploymentAccessError(str(deployment_id))
    return deployment


async def pause_deployment(
    repo: PaperControlRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    deployment_id: UUID,
    idempotency_key: str,
) -> PaperDeploymentView:
    cached = await _idempotent_or_none(repo, deployment_id, idempotency_key)
    if cached is not None:
        return cached

    deployment = await _load_owned_deployment(
        repo, tenant_id=tenant_id, deployment_id=deployment_id
    )
    if deployment.state != DeploymentState.RUNNING:
        if deployment.state == DeploymentState.PAUSED:
            # A concurrent same-key PAUSE may have already won before this call even
            # reached `increment_fence()` -- check for its command row (with the
            # bounded wait above) before concluding this is a genuine non-RUNNING
            # rejection rather than this request's own retry.
            cached = await _idempotent_or_none_awaiting_concurrent_winner(
                repo, deployment_id, idempotency_key
            )
            if cached is not None:
                return cached
        raise InvalidDeploymentStateError(f"{deployment.state.value}에서는 정지할 수 없습니다.")

    try:
        updated = await repo.increment_fence(
            deployment_id,
            tenant_id=tenant_id,
            expected_state=DeploymentState.RUNNING.value,
            new_state=DeploymentState.PAUSED.value,
        )
    except ConcurrencyConflictError:
        # Stability audit A6-G5-4 (task-10780): asymmetric to stop_deployment's
        # retry-after-check path (§2 "STOP and risk/emergency PAUSE take
        # precedence"). When a retry of the same idempotency_key PAUSE loses the
        # race and is immediately converted to failure, the initial request that
        # has already transitioned to PAUSED still receives an error — violating
        # PAP-006 "duplicate command is idempotent" principle. Re-read and if
        # already PAUSED return that result (without creating a duplicate command
        # row — the first successful request already recorded it). If not PAUSED
        # (e.g., STOP preempted first), this PAUSE intent failed and remains
        # failed.
        refreshed = await repo.get_deployment(deployment_id)
        assert refreshed is not None
        if refreshed.state == DeploymentState.PAUSED:
            return deployment_to_view(refreshed)
        raise InvalidDeploymentStateError("다른 요청이 먼저 상태를 바꿨습니다.") from None

    await repo.insert_command(
        deployment_id=deployment_id,
        idempotency_key=idempotency_key,
        command_type=CommandType.PAUSE,
        actor_subject_id=actor_subject_id,
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    return deployment_to_view(updated)


async def stop_deployment(
    repo: PaperControlRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    deployment_id: UUID,
    idempotency_key: str,
) -> PaperDeploymentView:
    cached = await _idempotent_or_none(repo, deployment_id, idempotency_key)
    if cached is not None:
        return cached

    deployment = await _load_owned_deployment(
        repo, tenant_id=tenant_id, deployment_id=deployment_id
    )
    if deployment.state not in _STOPPABLE_STATES:
        raise InvalidDeploymentStateError(f"{deployment.state.value}에서는 정지할 수 없습니다.")

    try:
        updated = await repo.increment_fence(
            deployment_id,
            tenant_id=tenant_id,
            expected_state=deployment.state.value,
            new_state=DeploymentState.STOPPED.value,
        )
    except ConcurrencyConflictError:
        # PAP-003 "simultaneous start/stop results in STOPPED" — if another
        # request already changed the state (e.g., just transitioned to RUNNING),
        # re-read the latest state and retry from there. STOP "takes precedence"
        # principle (§2) is implemented here via retry — already STOPPED/FAILED
        # remains idempotent.
        refreshed = await repo.get_deployment(deployment_id)
        assert refreshed is not None
        if refreshed.state in (DeploymentState.STOPPED, DeploymentState.FAILED):
            return deployment_to_view(refreshed)
        if refreshed.state not in _STOPPABLE_STATES:
            raise InvalidDeploymentStateError(
                f"{refreshed.state.value}에서는 정지할 수 없습니다."
            ) from None
        updated = await repo.increment_fence(
            deployment_id,
            tenant_id=tenant_id,
            expected_state=refreshed.state.value,
            new_state=DeploymentState.STOPPED.value,
        )

    await repo.insert_command(
        deployment_id=deployment_id,
        idempotency_key=idempotency_key,
        command_type=CommandType.STOP,
        actor_subject_id=actor_subject_id,
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    return deployment_to_view(updated)
