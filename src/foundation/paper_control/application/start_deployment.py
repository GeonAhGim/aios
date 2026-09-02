"""StartDeployment/ResumeDeployment 공용 게이트 재평가.

Spec: AIOSproject 77번 §2/§3 — "RUNNING requires current trust, active
mandate, paper-eligible non-expired package, fresh policy/risk decision,
healthy connection/data/reconciliation, and verified paper provenance."

package/reconciliation freshness는 71번 §4 Contract ownership 경계 밖(FND-04
package lifecycle과 FND-08 reconciliation이 아직 없음, 마이그레이션
docstring 참조) — 이 리프가 실제로 재확인하는 건 risk_gate(FND-06)의
DEPLOYMENT 게이트(mandate+safety control 합성)와, connection이 지정됐다면
그 freshness뿐이다."""
from __future__ import annotations

from uuid import UUID

from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.application.request_deployment import deployment_to_view
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.domain.models import CommandOutcome, CommandType, DeploymentState
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.domain.models import GateKind
from src.foundation.risk_gate.ports.repository import RiskGateRepository


class DeploymentNotFoundError(Exception):
    pass


class CrossTenantDeploymentAccessError(Exception):
    pass


class InvalidDeploymentStateError(Exception):
    pass


class RiskGateDeniedError(Exception):
    def __init__(self, reason_codes: list[str]) -> None:
        super().__init__(f"risk gate가 배포를 거부했습니다: {reason_codes}")
        self.reason_codes = reason_codes


async def _start_or_resume(
    repo: PaperControlRepository,
    risk_repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    deployment_id: UUID,
    idempotency_key: str,
    command_type: CommandType,
    expected_state: DeploymentState,
) -> PaperDeploymentView:
    existing_command = await repo.get_command_by_idempotency_key(deployment_id, idempotency_key)
    if existing_command is not None:
        current = await repo.get_deployment(deployment_id)
        assert current is not None
        return deployment_to_view(current)

    deployment = await repo.get_deployment(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError(str(deployment_id))
    if deployment.tenant_id != tenant_id:
        raise CrossTenantDeploymentAccessError(str(deployment_id))
    if deployment.state != expected_state:
        raise InvalidDeploymentStateError(
            f"{deployment.state.value}에서는 {command_type.value}할 수 없습니다."
        )

    risk_result = await evaluate_risk_gate(
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        gate_kind=GateKind.DEPLOYMENT,
        connection_id=deployment.connection_id,
    )
    if risk_result.outcome.value != "ALLOW":
        await repo.insert_command(
            deployment_id=deployment_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_subject_id=actor_subject_id,
            outcome=CommandOutcome.DENIED,
            detail=",".join(risk_result.reason_codes),
        )
        raise RiskGateDeniedError(risk_result.reason_codes)

    updated = await repo.transition_deployment_state(
        deployment_id, expected_state=expected_state.value, new_state=DeploymentState.RUNNING.value
    )
    await repo.insert_command(
        deployment_id=deployment_id,
        idempotency_key=idempotency_key,
        command_type=command_type,
        actor_subject_id=actor_subject_id,
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    return deployment_to_view(updated)


async def start_deployment(
    repo: PaperControlRepository,
    risk_repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    deployment_id: UUID,
    idempotency_key: str,
) -> PaperDeploymentView:
    return await _start_or_resume(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
        deployment_id=deployment_id,
        idempotency_key=idempotency_key,
        command_type=CommandType.START,
        expected_state=DeploymentState.READY,
    )


async def resume_deployment(
    repo: PaperControlRepository,
    risk_repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    deployment_id: UUID,
    idempotency_key: str,
) -> PaperDeploymentView:
    """77번 §3 "Resume: PAUSED only + complete reevaluation" — start와 완전히
    같은 게이트 재평가를 거친다(단축 경로 없음)."""
    return await _start_or_resume(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
        deployment_id=deployment_id,
        idempotency_key=idempotency_key,
        command_type=CommandType.RESUME,
        expected_state=DeploymentState.PAUSED,
    )
