"""PauseDeployment/StopDeployment 공용 — 둘 다 fence token을 증가시켜
진행 중인(또는 미래의) tick/intent를 무효화한다.

Spec: AIOSproject 77번 §3 "Pause: fence token increments, cancel future
ticks/intents" / "Stop: terminal stop, cancel work". "STOP과 risk/emergency
PAUSE는 START/RESUME보다 우선한다"(77번 §2)는 이 코드베이스에 아직 진짜
동시 커맨드 스케줄러가 없어 idempotency_key 유일성으로 순서를 보장하는
선까지만 구현한다(PAP-003의 완전한 "동시 시작/정지" 재현은 105번 §4
형태 A 테스트로 검증)."""
from __future__ import annotations

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
        raise InvalidDeploymentStateError(f"{deployment.state.value}에서는 정지할 수 없습니다.")

    try:
        updated = await repo.increment_fence(
            deployment_id,
            expected_state=DeploymentState.RUNNING.value,
            new_state=DeploymentState.PAUSED.value,
        )
    except ConcurrencyConflictError:
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
            expected_state=deployment.state.value,
            new_state=DeploymentState.STOPPED.value,
        )
    except ConcurrencyConflictError:
        # PAP-003 "simultaneous start/stop results in STOPPED" — 다른 요청이
        # 먼저 상태를 바꿨다면(예: RUNNING으로 막 전이) 최신 상태를 다시 읽어
        # 그 상태에서 재시도한다. STOP은 "우선한다"는 원칙(77번 §2)을 여기서
        # 재시도로 구현한다 — 이미 STOPPED/FAILED라면 그대로 idempotent.
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
