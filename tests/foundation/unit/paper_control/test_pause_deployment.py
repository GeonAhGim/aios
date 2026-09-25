"""FND-07 pause_deployment/stop_deployment 순수 단위테스트 — DB 없음, fake repo.

pause_deployment/stop_deployment의 커버되지 않은 분기(멱등 캐시 히트,
미존재/타테넌트/잘못된 상태, ConcurrencyConflictError 실패주입과 STOP의
재시도 경로 3가지)를 fake PaperControlRepository로 검증한다."""
from __future__ import annotations

import dataclasses
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.paper_control.application.pause_deployment import (
    CrossTenantDeploymentAccessError,
    DeploymentNotFoundError,
    InvalidDeploymentStateError,
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentCommand,
    DeploymentState,
    PaperDeployment,
)

_TENANT_ID = uuid4()
_DEPLOYMENT_ID = uuid4()
_ACTOR_ID = uuid4()


def _deployment(
    state: DeploymentState, fence_token: int = 0, **overrides: object
) -> PaperDeployment:
    defaults: dict[str, Any] = dict(
        id=_DEPLOYMENT_ID,
        tenant_id=_TENANT_ID,
        connection_id=None,
        package_ref="pkg-1",
        mandate_revision_id=uuid4(),
        provenance=AdapterProvenance(
            adapter_type="fake-paper-v1",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="SANDBOX",
            provider_sandbox_account_ref="sandbox-acct-1",
        ),
        state=state,
        fence_token=fence_token,
    )
    defaults.update(overrides)
    return PaperDeployment(**defaults)


class FakeRepo:
    """Minimal fake implementing only the methods pause/stop_deployment call."""

    def __init__(self, deployment: PaperDeployment | None) -> None:
        self.deployment = deployment
        self.commands: dict[str, DeploymentCommand] = {}
        self.get_deployment_queue: list[PaperDeployment | None] | None = None
        self.increment_fence_side_effects: list[Exception | None] = []
        self.increment_fence_calls: list[tuple[str, str]] = []
        self.insert_command_calls: list[DeploymentCommand] = []

    async def get_deployment(self, deployment_id: UUID) -> PaperDeployment | None:
        if self.get_deployment_queue is not None:
            return self.get_deployment_queue.pop(0)
        return self.deployment

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        return self.commands.get(idempotency_key)

    async def increment_fence(
        self, deployment_id: UUID, *, expected_state: str, new_state: str
    ) -> PaperDeployment:
        self.increment_fence_calls.append((expected_state, new_state))
        if self.increment_fence_side_effects:
            effect = self.increment_fence_side_effects.pop(0)
            if effect is not None:
                raise effect
        assert self.deployment is not None
        updated = dataclasses.replace(
            self.deployment,
            state=DeploymentState(new_state),
            fence_token=self.deployment.fence_token + 1,
        )
        self.deployment = updated
        return updated

    async def insert_command(
        self,
        *,
        deployment_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        actor_subject_id: UUID,
        outcome: CommandOutcome,
        detail: str | None,
    ) -> DeploymentCommand:
        cmd = DeploymentCommand(
            id=uuid4(),
            deployment_id=deployment_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_subject_id=actor_subject_id,
            outcome=outcome,
            detail=detail,
        )
        self.commands[idempotency_key] = cmd
        self.insert_command_calls.append(cmd)
        return cmd


# ---- pause_deployment ----------------------------------------------------


async def test_pause_deployment_happy_path_increments_fence():
    repo = FakeRepo(_deployment(DeploymentState.RUNNING, fence_token=0))
    view = await pause_deployment(
        repo,
        tenant_id=_TENANT_ID,
        actor_subject_id=_ACTOR_ID,
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="pause-1",
    )
    assert view.state.value == "PAUSED"
    assert view.fence_token == 1
    assert len(repo.insert_command_calls) == 1
    assert repo.insert_command_calls[0].command_type == CommandType.PAUSE


async def test_pause_deployment_returns_cached_view_on_duplicate_idempotency_key():
    """PAP-006 — 같은 idempotency_key로 재요청하면 재평가 없이 캐시된 결과를 반환한다."""
    deployment = _deployment(DeploymentState.PAUSED, fence_token=1)
    repo = FakeRepo(deployment)
    repo.commands["pause-dup"] = DeploymentCommand(
        id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="pause-dup",
        command_type=CommandType.PAUSE,
        actor_subject_id=_ACTOR_ID,
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    view = await pause_deployment(
        repo,
        tenant_id=_TENANT_ID,
        actor_subject_id=_ACTOR_ID,
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="pause-dup",
    )
    assert view.state.value == "PAUSED"
    assert view.fence_token == 1
    assert repo.increment_fence_calls == []
    assert repo.insert_command_calls == []


async def test_pause_deployment_raises_when_deployment_not_found():
    repo = FakeRepo(None)
    with pytest.raises(DeploymentNotFoundError):
        await pause_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="pause-1",
        )


async def test_pause_deployment_raises_on_cross_tenant_access():
    other_tenant = uuid4()
    repo = FakeRepo(_deployment(DeploymentState.RUNNING, tenant_id=other_tenant))
    with pytest.raises(CrossTenantDeploymentAccessError):
        await pause_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="pause-1",
        )


@pytest.mark.parametrize(
    "state",
    [
        DeploymentState.REQUESTED,
        DeploymentState.READY,
        DeploymentState.PAUSED,
        DeploymentState.STOPPED,
        DeploymentState.FAILED,
    ],
)
async def test_pause_deployment_raises_on_non_running_state(state: DeploymentState):
    repo = FakeRepo(_deployment(state))
    with pytest.raises(InvalidDeploymentStateError):
        await pause_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="pause-1",
        )


async def test_pause_deployment_raises_on_concurrent_conflict():
    """실패주입 — increment_fence가 ConcurrencyConflictError를 던지면
    pause는 재시도 없이 InvalidDeploymentStateError로 변환한다."""
    repo = FakeRepo(_deployment(DeploymentState.RUNNING))
    repo.increment_fence_side_effects = [ConcurrencyConflictError()]
    with pytest.raises(InvalidDeploymentStateError):
        await pause_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="pause-1",
        )
    assert repo.insert_command_calls == []


# ---- stop_deployment -------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        DeploymentState.READY,
        DeploymentState.RUNNING,
        DeploymentState.PAUSED,
        DeploymentState.DEGRADED,
        DeploymentState.RECOVERY_REVIEW,
    ],
)
async def test_stop_deployment_happy_path_from_any_stoppable_state(state: DeploymentState):
    repo = FakeRepo(_deployment(state, fence_token=3))
    view = await stop_deployment(
        repo,
        tenant_id=_TENANT_ID,
        actor_subject_id=_ACTOR_ID,
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="stop-1",
    )
    assert view.state.value == "STOPPED"
    assert view.fence_token == 4
    assert len(repo.insert_command_calls) == 1
    assert repo.insert_command_calls[0].command_type == CommandType.STOP


async def test_stop_deployment_returns_cached_view_on_duplicate_idempotency_key():
    deployment = _deployment(DeploymentState.STOPPED, fence_token=2)
    repo = FakeRepo(deployment)
    repo.commands["stop-dup"] = DeploymentCommand(
        id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="stop-dup",
        command_type=CommandType.STOP,
        actor_subject_id=_ACTOR_ID,
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    view = await stop_deployment(
        repo,
        tenant_id=_TENANT_ID,
        actor_subject_id=_ACTOR_ID,
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="stop-dup",
    )
    assert view.state.value == "STOPPED"
    assert repo.increment_fence_calls == []


async def test_stop_deployment_raises_when_deployment_not_found():
    repo = FakeRepo(None)
    with pytest.raises(DeploymentNotFoundError):
        await stop_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="stop-1",
        )


async def test_stop_deployment_raises_on_cross_tenant_access():
    other_tenant = uuid4()
    repo = FakeRepo(_deployment(DeploymentState.RUNNING, tenant_id=other_tenant))
    with pytest.raises(CrossTenantDeploymentAccessError):
        await stop_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="stop-1",
        )


async def test_stop_deployment_raises_on_non_stoppable_state():
    repo = FakeRepo(_deployment(DeploymentState.REQUESTED))
    with pytest.raises(InvalidDeploymentStateError):
        await stop_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="stop-1",
        )


async def test_stop_deployment_conflict_then_already_stopped_is_idempotent():
    """PAP-003 — 동시 STOP 경쟁에서 다른 요청이 먼저 STOPPED로 바꿨다면
    재시도 없이 그 결과를 그대로 반환하고 insert_command는 다시 하지 않는다."""
    repo = FakeRepo(_deployment(DeploymentState.RUNNING, fence_token=5))
    repo.increment_fence_side_effects = [ConcurrencyConflictError()]
    repo.get_deployment_queue = [
        _deployment(DeploymentState.RUNNING, fence_token=5),  # _load_owned_deployment
        _deployment(DeploymentState.STOPPED, fence_token=6),  # refreshed after conflict
    ]
    view = await stop_deployment(
        repo,
        tenant_id=_TENANT_ID,
        actor_subject_id=_ACTOR_ID,
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="stop-1",
    )
    assert view.state.value == "STOPPED"
    assert view.fence_token == 6
    assert repo.insert_command_calls == []


async def test_stop_deployment_conflict_then_refreshed_state_not_stoppable_raises():
    """실패주입 — 재조회한 상태가 정지 불가 상태(FAILED가 아니면서
    _STOPPABLE_STATES에도 없는 경우는 없지만, 여기서는 REQUESTED로
    시뮬레이션)면 InvalidDeploymentStateError로 변환한다."""
    repo = FakeRepo(_deployment(DeploymentState.RUNNING, fence_token=0))
    repo.increment_fence_side_effects = [ConcurrencyConflictError()]
    repo.get_deployment_queue = [
        _deployment(DeploymentState.RUNNING, fence_token=0),
        _deployment(DeploymentState.REQUESTED, fence_token=0),
    ]
    with pytest.raises(InvalidDeploymentStateError):
        await stop_deployment(
            repo,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="stop-1",
        )
    assert repo.insert_command_calls == []


async def test_stop_deployment_conflict_then_refreshed_stoppable_retries_and_succeeds():
    """실패주입 — 첫 increment_fence만 충돌하고, 재조회한 상태가 여전히
    정지 가능하면 그 상태를 기준으로 재시도해 STOPPED에 도달한다."""
    repo = FakeRepo(_deployment(DeploymentState.RUNNING, fence_token=0))
    repo.increment_fence_side_effects = [ConcurrencyConflictError(), None]
    repo.get_deployment_queue = [
        _deployment(DeploymentState.RUNNING, fence_token=0),
        _deployment(DeploymentState.PAUSED, fence_token=1),
    ]
    view = await stop_deployment(
        repo,
        tenant_id=_TENANT_ID,
        actor_subject_id=_ACTOR_ID,
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="stop-1",
    )
    assert view.state.value == "STOPPED"
    assert repo.increment_fence_calls == [
        ("RUNNING", "STOPPED"),
        ("PAUSED", "STOPPED"),
    ]
    assert len(repo.insert_command_calls) == 1
