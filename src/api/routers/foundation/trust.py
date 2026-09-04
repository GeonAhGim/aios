"""Trust Core API — 71번 §6 규칙: router는 auth/TenantContext 주입/transport
validation/command invocation만 담당한다. policy/risk/package 판단을 두지
않는다.

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다(§9 PLT-21b decision,
task-1218). 단 `revoke_consent()`가 postgres_repository.py에서 그대로
전파시키는 raw `LookupError`만 예외 — application 모듈(revoke_consent.py)은
이 리프의 파일 목록 밖이라 고칠 수 없어, 여기서 이름 있는 예외
(`exception_mapping.ConsentNotFoundError`)로 감싼다."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.exception_mapping import ConsentNotFoundError
from src.api.foundation_deps import get_tenant_context, get_trust_repository
from src.api.schemas.foundation.trust import AcceptDisclosureRequest, TrustStatusResponse
from src.foundation.trust.application.accept_disclosure import accept_disclosure
from src.foundation.trust.application.revoke_consent import revoke_consent
from src.foundation.trust.contracts.v1 import ConsentDecision, TenantContext
from src.foundation.trust.ports.repository import TrustRepository
from src.foundation.trust.projections import build_trust_status_view

router = APIRouter(prefix="/v1/foundation/trust", tags=["foundation:trust"])


@router.get("/status")
async def get_trust_status(
    context: TenantContext = Depends(get_tenant_context),
    repo: TrustRepository = Depends(get_trust_repository),
) -> ApiResponse[TrustStatusResponse]:
    view = await build_trust_status_view(repo, context.tenant_id)
    return ok(
        TrustStatusResponse(tenant_id=view.tenant_id, consents=view.consents, as_of=view.as_of)
    )


@router.post("/consents", status_code=status.HTTP_201_CREATED)
async def post_accept_disclosure(
    body: AcceptDisclosureRequest,
    context: TenantContext = Depends(get_tenant_context),
    repo: TrustRepository = Depends(get_trust_repository),
) -> ApiResponse[ConsentDecision]:
    result = await accept_disclosure(
        repo, context, purpose=body.purpose, disclosure_revision=body.disclosure_revision
    )
    return ok(result)


@router.post("/consents/{consent_id}:revoke")
async def post_revoke_consent(
    consent_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: TrustRepository = Depends(get_trust_repository),
) -> ApiResponse[ConsentDecision]:
    try:
        result = await revoke_consent(repo, context, consent_id=consent_id)
    except LookupError as exc:
        raise ConsentNotFoundError(str(exc)) from exc
    return ok(result)
