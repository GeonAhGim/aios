"""Trust Core API — 71번 §6 규칙: router는 auth/TenantContext 주입/transport
validation/command invocation만 담당한다. policy/risk/package 판단을 두지
않는다."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.foundation_deps import get_tenant_context, get_trust_repository
from src.api.schemas.foundation.trust import AcceptDisclosureRequest, TrustStatusResponse
from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.application.accept_disclosure import (
    ConsentAlreadyActiveError,
    DisclosureNotFoundError,
    DisclosureRetiredError,
    accept_disclosure,
)
from src.foundation.trust.application.revoke_consent import (
    CrossTenantConsentAccessError,
    revoke_consent,
)
from src.foundation.trust.contracts.v1 import ConsentDecision, TenantContext
from src.foundation.trust.ports.repository import TrustRepository
from src.foundation.trust.projections import build_trust_status_view

router = APIRouter(prefix="/v1/foundation/trust", tags=["foundation:trust"])


@router.get("/status")
async def get_trust_status(
    context: TenantContext = Depends(get_tenant_context),
    repo: TrustRepository = Depends(get_trust_repository),
) -> TrustStatusResponse:
    view = await build_trust_status_view(repo, context.tenant_id)
    return TrustStatusResponse(tenant_id=view.tenant_id, consents=view.consents, as_of=view.as_of)


@router.post("/consents", status_code=status.HTTP_201_CREATED)
async def post_accept_disclosure(
    body: AcceptDisclosureRequest,
    context: TenantContext = Depends(get_tenant_context),
    repo: TrustRepository = Depends(get_trust_repository),
) -> ConsentDecision:
    try:
        return await accept_disclosure(
            repo, context, purpose=body.purpose, disclosure_revision=body.disclosure_revision
        )
    except DisclosureNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "해당 disclosure를 찾을 수 없습니다."
        ) from exc
    except DisclosureRetiredError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "폐기된 disclosure에는 동의할 수 없습니다."
        ) from exc
    except ConsentAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 동의된 상태입니다.") from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/consents/{consent_id}:revoke")
async def post_revoke_consent(
    consent_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: TrustRepository = Depends(get_trust_repository),
) -> ConsentDecision:
    try:
        return await revoke_consent(repo, context, consent_id=consent_id)
    except CrossTenantConsentAccessError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "다른 사용자의 동의입니다.") from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 동의입니다.") from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
