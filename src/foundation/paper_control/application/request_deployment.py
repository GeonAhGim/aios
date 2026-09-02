"""RequestDeployment(+Prepare) 커맨드.

Spec: AIOSproject 47번 §3, 77번 §2/§3.

스콥 축소(명시, 마이그레이션 docstring 참조): 77번 §2는 REQUESTED->PREPARING
->READY를 별도 단계로 나누지만, 이 리프는 두 전이 사이에 실제 비동기
대기(외부 provider 승인 등)가 없어 REQUEST와 PREPARE를 한 커맨드로 합친다
— provenance가 유효하고 mandate가 ACTIVE면 즉시 READY, 아니면 FAILED.

package_ref는 불투명 문자열로만 받는다 — FND-04(strategy_packages)가 아직
PAPER_ELIGIBLE 패키지 lifecycle 자체를 구현하지 않아(validation-run까지만
있음), 이 리프에서 package 유효성을 실제로 검증하지 못한다. package
lifecycle이 생기면 이 함수의 package 검증 부분만 교체하면 된다."""
from __future__ import annotations

from uuid import UUID, uuid4

from src.foundation.mandates.domain.models import MandateRevisionState
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.contracts.v1 import DeploymentState as ContractState
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.paper_control.domain.rules import InvalidProvenanceError, validate_provenance
from src.foundation.paper_control.ports.repository import PaperControlRepository


class NoActiveMandateError(Exception):
    pass


def deployment_to_view(deployment: PaperDeployment) -> PaperDeploymentView:
    return PaperDeploymentView(
        id=deployment.id,
        package_ref=deployment.package_ref,
        connection_id=deployment.connection_id,
        state=ContractState(deployment.state.value),
        fence_token=deployment.fence_token,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


async def request_deployment(
    repo: PaperControlRepository,
    mandate_repo: MandateRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    package_ref: str,
    connection_id: UUID | None,
    adapter_type: str,
    provider_sandbox_account_ref: str,
    endpoint_classification: str,
    idempotency_key: str,
) -> PaperDeploymentView:
    mandate = await mandate_repo.get_mandate(tenant_id)
    if mandate is None or mandate.active_revision_id is None:
        raise NoActiveMandateError(str(tenant_id))
    revision = await mandate_repo.get_revision(mandate.active_revision_id)
    if revision is None or revision.state != MandateRevisionState.ACTIVE:
        raise NoActiveMandateError(str(tenant_id))

    provenance = AdapterProvenance(
        adapter_type=adapter_type,
        credential_class=CredentialClass.PAPER,
        endpoint_classification=endpoint_classification,
        provider_sandbox_account_ref=provider_sandbox_account_ref,
    )

    # 이 리프는 REQUESTED 중간 행을 별도로 남기지 않는다(위 스콥 축소 —
    # REQUEST+PREPARE를 한 커맨드로 합침) — provenance 검증 결과로 곧장
    # READY 또는 FAILED 행 하나만 만든다.
    deployment_id = uuid4()
    detail: str | None = None
    try:
        validate_provenance(provenance)
        final_state = DeploymentState.READY
        outcome = CommandOutcome.ACCEPTED
    except InvalidProvenanceError as exc:
        final_state = DeploymentState.FAILED
        outcome = CommandOutcome.DENIED
        detail = str(exc)

    deployment = await repo.insert_deployment(
        PaperDeployment(
            id=deployment_id,
            tenant_id=tenant_id,
            connection_id=connection_id,
            package_ref=package_ref,
            mandate_revision_id=revision.id,
            provenance=provenance,
            state=final_state,
            fence_token=0,
        )
    )
    await repo.insert_command(
        deployment_id=deployment.id,
        idempotency_key=idempotency_key,
        command_type=CommandType.REQUEST,
        actor_subject_id=actor_subject_id,
        outcome=outcome,
        detail=detail,
    )
    if outcome is CommandOutcome.DENIED:
        raise InvalidProvenanceError(detail or "provenance 검증 실패")
    return deployment_to_view(deployment)
