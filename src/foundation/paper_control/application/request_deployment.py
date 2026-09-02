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

import hashlib
import json
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


class IdempotencyKeyConflictError(Exception):
    """PAP-006 — 같은 idempotency_key로 이전과 다른 내용의 REQUEST가 왔다.
    진짜 idempotency는 "같은 요청의 재시도"만 캐시해야 한다 — 다른 요청에
    키를 잘못 재사용한 클라이언트 버그를 조용히 삼켜 엉뚱한 배포를 돌려주면
    안 된다(전수감사 agent-platform-12 발견)."""


def _compute_request_digest(
    *,
    package_ref: str,
    connection_id: UUID | None,
    adapter_type: str,
    provider_sandbox_account_ref: str,
    endpoint_classification: str,
) -> str:
    payload = {
        "package_ref": package_ref,
        "connection_id": str(connection_id) if connection_id is not None else None,
        "adapter_type": adapter_type,
        "provider_sandbox_account_ref": provider_sandbox_account_ref,
        "endpoint_classification": endpoint_classification,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def _replay_or_conflict(
    repo: PaperControlRepository,
    existing: PaperDeployment,
    *,
    digest: str,
    idempotency_key: str,
) -> PaperDeploymentView:
    if existing.request_digest != digest:
        raise IdempotencyKeyConflictError(
            f"idempotency_key={idempotency_key}는 이전과 다른 요청 내용에 이미 "
            "쓰였습니다."
        )
    if existing.state == DeploymentState.FAILED:
        # FAILED 결과도 최초 응답을 그대로 재현한다(원래 request_deployment()가
        # 이 경우 예외를 던졌으므로, 재시도도 같은 예외를 받아야 한다).
        command = await repo.get_command_by_idempotency_key(existing.id, idempotency_key)
        detail = command.detail if command is not None else None
        raise InvalidProvenanceError(detail or "provenance 검증 실패")
    return deployment_to_view(existing)


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
    digest = _compute_request_digest(
        package_ref=package_ref,
        connection_id=connection_id,
        adapter_type=adapter_type,
        provider_sandbox_account_ref=provider_sandbox_account_ref,
        endpoint_classification=endpoint_classification,
    )
    # PAP-006 — 같은 (tenant_id, idempotency_key)로 이미 만들어진 deployment가
    # 있으면 새로 만들지 않는다(전수감사 발견 — 이전에는 이 확인 자체가 없어
    # 매 재시도가 새 deployment를 만들었다).
    existing = await repo.get_deployment_by_request_key(tenant_id, idempotency_key)
    if existing is not None:
        return await _replay_or_conflict(
            repo, existing, digest=digest, idempotency_key=idempotency_key
        )

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
            request_idempotency_key=idempotency_key,
            request_digest=digest,
        )
    )
    if deployment.id != deployment_id:
        # 위의 사전 조회(get_deployment_by_request_key) 이후 이 INSERT 사이의
        # 좁은 창에서 진짜 동시 요청에 졌다 — insert_deployment()가 이미
        # ON CONFLICT DO NOTHING + 재조회로 승자의 행을 돌려줬다. 커맨드는
        # 승자 쪽이 이미 기록했으니 여기서 다시 쓰지 않는다.
        return await _replay_or_conflict(
            repo, deployment, digest=digest, idempotency_key=idempotency_key
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
