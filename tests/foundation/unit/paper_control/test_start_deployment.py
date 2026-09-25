"""FND-07 start_deployment/resume_deployment 순수 단위테스트 — DB 없음, fake repo.

start_deployment(READY→RUNNING)/resume_deployment(PAUSED→RUNNING)의
공유 게이트 재평가 경로(멱등 캐시 히트, 미존재/타테넌트/잘못된 상태,
risk_gate DENY 실패주입)를 fake PaperControlRepository + patched
evaluate_risk_gate로 검증한다."""
from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from src.foundation.paper_control.application.start_deployment import (
    CrossTenantDeploymentAccessError,
    DeploymentNotFoundError,
    InvalidDeploymentStateError,
    RiskGateDeniedError,
    resume_deployment,
    start_deployment,
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


class _FakeRiskResult:
    def __init__(self, outcome: str, reason_codes: list[str] | None = None) -> None:
        self.outcome = _FakeOutcome(outcome)
        self.reason_codes = reason_codes or []


class _FakeOutcome:
    def __init__(self, value: str) -> None:
        self.value = value


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
    """Minimal fake implementing only the methods start/resume_deployment call."""

    def __init__(self, deployment: PaperDeployment | None) -> None:
        self.deployment = deployment
        self.commands: dict[str, DeploymentCommand] = {}
        self.transition_side_effect: Exception | None = None
        self.transition_calls: list[tuple[str, str]] = []
        self.insert_command_calls: list[DeploymentCommand] = []

    async def get_deployment(self, deployment_id: UUID) -> PaperDeployment | None:
        return self.deployment

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        return self.commands.get(idempotency_key)

    async def transition_deployment_state(
        self, deployment_id: UUID, *, expected_state: str, new_state: str
    ) -> PaperDeployment:
        self.transition_calls.append((expected_state, new_state))
        if self.transition_side_effect is not None:
            raise self.transition_side_effect
        assert self.deployment is not None
        updated = dataclasses.replace(self.deployment, state=DeploymentState(new_state))
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


def _patched_risk_gate(outcome: str = "ALLOW", reason_codes: list[str] | None = None):
    return patch(
        "src.foundation.paper_control.application.start_deployment.evaluate_risk_gate",
        AsyncMock(return_value=_FakeRiskResult(outcome, reason_codes)),
    )


# ---- start_deployment -------------------------------------------------------


async def test_start_deployment_happy_path_transitions_to_running():
    repo = FakeRepo(_deployment(DeploymentState.READY))
    with _patched_risk_gate("ALLOW"):
        view = await start_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="start-1",
        )
    assert view.state.value == "RUNNING"
    assert repo.transition_calls == [("READY", "RUNNING")]
    assert len(repo.insert_command_calls) == 1
    assert repo.insert_command_calls[0].command_type == CommandType.START
    assert repo.insert_command_calls[0].outcome == CommandOutcome.ACCEPTED


async def test_start_deployment_returns_cached_view_on_duplicate_idempotency_key():
    """PAP-006 — 같은 idempotency_key로 재요청하면 재평가 없이 캐시된 결과를 반환한다."""
    deployment = _deployment(DeploymentState.RUNNING)
    repo = FakeRepo(deployment)
    repo.commands["start-dup"] = DeploymentCommand(
        id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        idempotency_key="start-dup",
        command_type=CommandType.START,
        actor_subject_id=_ACTOR_ID,
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    with _patched_risk_gate("ALLOW"):
        view = await start_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="start-dup",
        )
    assert view.state.value == "RUNNING"
    assert repo.transition_calls == []
    assert repo.insert_command_calls == []


async def test_start_deployment_raises_when_deployment_not_found():
    repo = FakeRepo(None)
    with pytest.raises(DeploymentNotFoundError):
        await start_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="start-1",
        )


async def test_start_deployment_raises_on_cross_tenant_access():
    other_tenant = uuid4()
    repo = FakeRepo(_deployment(DeploymentState.READY, tenant_id=other_tenant))
    with pytest.raises(CrossTenantDeploymentAccessError):
        await start_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="start-1",
        )


@pytest.mark.parametrize(
    "state",
    [
        DeploymentState.REQUESTED,
        DeploymentState.RUNNING,
        DeploymentState.PAUSED,
        DeploymentState.STOPPED,
        DeploymentState.FAILED,
    ],
)
async def test_start_deployment_raises_on_non_ready_state(state: DeploymentState):
    repo = FakeRepo(_deployment(state))
    with pytest.raises(InvalidDeploymentStateError):
        await start_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="start-1",
        )


async def test_start_deployment_raises_when_risk_gate_denies():
    """실패주입 — risk_gate가 ALLOW 이외를 반환하면 DENIED 커맨드를 기록하고
    RiskGateDeniedError로 변환한다(전이는 시도하지 않는다)."""
    repo = FakeRepo(_deployment(DeploymentState.READY))
    with _patched_risk_gate("DENY", ["NO_ACTIVE_MANDATE"]):
        with pytest.raises(RiskGateDeniedError) as excinfo:
            await start_deployment(
                repo,
                risk_repo=None,
                mandate_repo=None,
                connection_repo=None,
                tenant_id=_TENANT_ID,
                actor_subject_id=_ACTOR_ID,
                deployment_id=_DEPLOYMENT_ID,
                idempotency_key="start-1",
            )
    assert excinfo.value.reason_codes == ["NO_ACTIVE_MANDATE"]
    assert repo.transition_calls == []
    assert len(repo.insert_command_calls) == 1
    assert repo.insert_command_calls[0].outcome == CommandOutcome.DENIED
    assert repo.insert_command_calls[0].detail == "NO_ACTIVE_MANDATE"


async def test_start_deployment_propagates_transition_failure():
    """실패주입 — transition_deployment_state가 예외를 던지면 그대로 전파된다
    (start_deployment 자체는 재시도 로직을 갖지 않는다)."""
    repo = FakeRepo(_deployment(DeploymentState.READY))
    repo.transition_side_effect = RuntimeError("concurrent write conflict")
    with _patched_risk_gate("ALLOW"):
        with pytest.raises(RuntimeError, match="concurrent write conflict"):
            await start_deployment(
                repo,
                risk_repo=None,
                mandate_repo=None,
                connection_repo=None,
                tenant_id=_TENANT_ID,
                actor_subject_id=_ACTOR_ID,
                deployment_id=_DEPLOYMENT_ID,
                idempotency_key="start-1",
            )
    assert repo.insert_command_calls == []


# ---- resume_deployment -------------------------------------------------------


async def test_resume_deployment_happy_path_transitions_to_running():
    repo = FakeRepo(_deployment(DeploymentState.PAUSED))
    with _patched_risk_gate("ALLOW"):
        view = await resume_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="resume-1",
        )
    assert view.state.value == "RUNNING"
    assert repo.transition_calls == [("PAUSED", "RUNNING")]
    assert repo.insert_command_calls[0].command_type == CommandType.RESUME


async def test_resume_deployment_raises_on_non_paused_state():
    """resume은 START와 달리 READY가 아니라 PAUSED만 허용한다."""
    repo = FakeRepo(_deployment(DeploymentState.READY))
    with pytest.raises(InvalidDeploymentStateError):
        await resume_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="resume-1",
        )


async def test_resume_deployment_raises_when_deployment_not_found():
    repo = FakeRepo(None)
    with pytest.raises(DeploymentNotFoundError):
        await resume_deployment(
            repo,
            risk_repo=None,
            mandate_repo=None,
            connection_repo=None,
            tenant_id=_TENANT_ID,
            actor_subject_id=_ACTOR_ID,
            deployment_id=_DEPLOYMENT_ID,
            idempotency_key="resume-1",
        )


async def test_resume_deployment_raises_when_risk_gate_denies():
    """실패주입 — resume 경로도 risk_gate DENY 시 재전이 없이 예외로 끝난다."""
    repo = FakeRepo(_deployment(DeploymentState.PAUSED))
    with _patched_risk_gate("PAUSE", ["DEGRADED_CONNECTION"]):
        with pytest.raises(RiskGateDeniedError):
            await resume_deployment(
                repo,
                risk_repo=None,
                mandate_repo=None,
                connection_repo=None,
                tenant_id=_TENANT_ID,
                actor_subject_id=_ACTOR_ID,
                deployment_id=_DEPLOYMENT_ID,
                idempotency_key="resume-1",
            )
    assert repo.transition_calls == []
