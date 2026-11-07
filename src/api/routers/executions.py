"""Module 16 — Execution dashboard API router (FD-16.1/16.2/16.3/16.4/16.6).

Spec: functional_design_v1.20.md#FD-16.1~FD-16.4/FD-16.6

FD-16.1 processing step ① ("verify allocatable balance relative to user account balance (FD-3.2)"):
the client does not send available_balance; the server computes it directly by querying
the actual exchange balance (FD-3.2) via CredentialResolver.

Deviation: this leaf has no HTTP endpoint that actually approves/rejects
LIVE execution approval (FD-10.1 pattern reuse) — approval decisions are admin actions
scoped to module 18 (admin tools); here we only honestly expose
the pending-approval state (approval_request_id, PENDING_APPROVAL).

PLT-19(task-1016): all raw HTTPException usages removed — ExecutionCreateError/
ExecutionControlError/CapitalAllocationError/CredentialNotFoundError are
transformed by the global handler (src/api/contracts/handlers.py) through
EXCEPTION_MAP in exception_mapping.py to the same status codes (400/404). Deferred:
wrapping this router's success responses follows the same rationale as PLT-17 decision
(see exchange_credentials.py module docstring).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_auth_service, get_current_user, reauthenticate
from src.api.execution_deps import (
    get_execution_monitoring_service,
    get_execution_service,
    get_live_readiness_service,
)
from src.api.schemas.execution import (
    ConvertToLiveRequest,
    ExecutionCardResponse,
    ExecutionCreateRequest,
    ExecutionResponse,
    ExpandExposureRequest,
    LiveConversionRequest,
    RetireRequest,
    SetMaxDrawdownRequest,
    to_execution_card_response,
    to_execution_response,
)
from src.api.schemas.live_readiness import LiveReadinessResponse, to_live_readiness_response
from src.api.service_deps import get_credential_resolver
from src.services.auth_service import AuthService, User
from src.services.credential_resolver import CredentialResolver
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService
from src.services.live_readiness_service import LiveReadinessService

router = APIRouter()


async def _available_balance(
    resolver: CredentialResolver, user_id: UUID, exchange: str, currency: str
) -> Decimal:
    adapter = await resolver.get_adapter(user_id, exchange)
    balances = await adapter.get_balance()
    for balance in balances:
        if balance.asset.upper() == currency.upper():
            return balance.available
    return Decimal("0")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_execution(
    body: ExecutionCreateRequest,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    available = await _available_balance(resolver, user.user_id, body.exchange, body.currency)
    summary = await service.create_execution(
        user.user_id,
        body.strategy_id,
        body.strategy_version,
        allocated_capital=body.allocated_capital,
        currency=body.currency,
        exchange=body.exchange,
        mode=body.mode,
        available_balance=available,
    )
    return to_execution_response(summary)


@router.get("")
async def list_executions(
    user: User = Depends(get_current_user),
    service: ExecutionMonitoringService = Depends(get_execution_monitoring_service),
) -> list[ExecutionCardResponse]:
    cards = await service.list_for_user(user.user_id)
    return [to_execution_card_response(card) for card in cards]


@router.get("/{execution_id}/live-readiness")
async def get_live_readiness(
    execution_id: int,
    user: User = Depends(get_current_user),
    service: LiveReadinessService = Depends(get_live_readiness_service),
) -> LiveReadinessResponse:
    """모의 -> 실전 전환 근거(J8 LP-2). 본인의 PAPER 실행만.
    자료가 없는 항목은 null이고 사유는 unavailable에 있다."""
    return to_live_readiness_response(await service.for_execution(execution_id, user.user_id))


@router.post("/{execution_id}/expand-exposure")
async def expand_exposure(
    execution_id: int,
    body: ExpandExposureRequest,
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    """J8 LP-4: 단계적 노출 확대. 자금 노출이 커지는 행동이라 재인증을 요구하고 자동 확대는 없다."""
    await reauthenticate(auth, user, body.password, body.totp_code)
    available = await _available_balance(resolver, user.user_id, body.exchange, body.currency)
    summary = await service.expand_exposure(
        execution_id,
        user.user_id,
        new_exposure_pct=body.exposure_pct,
        available_balance=available,
    )
    return to_execution_response(summary)


@router.post("/{execution_id}/start")
async def start_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    summary = await service.start(execution_id, user.user_id)
    return to_execution_response(summary)


@router.post("/{execution_id}/pause")
async def pause_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    summary = await service.pause(execution_id, paused_by="USER", user_id=user.user_id)
    return to_execution_response(summary)


@router.patch("/{execution_id}/risk-guard")
async def set_execution_risk_guard(
    execution_id: int,
    body: SetMaxDrawdownRequest,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    summary = await service.set_max_drawdown(execution_id, user.user_id, body.max_drawdown_pct)
    return to_execution_response(summary)


@router.post("/{execution_id}/retire")
async def retire_execution(
    execution_id: int,
    body: RetireRequest,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    summary = await service.retire(execution_id, user.user_id, liquidation=body.liquidation)
    return to_execution_response(summary)


@router.post(
    "/{execution_id}/convert-to-live",
    deprecated=True,
    status_code=status.HTTP_201_CREATED,  # v1 성공 선언 유지(폐기 표시이지 제거가 아니다)
    responses={status.HTTP_410_GONE: {"description": "폐기됨 -- /live-conversion을 사용한다"}},
)
async def convert_to_live(
    execution_id: int,
    body: ConvertToLiveRequest,
    user: User = Depends(get_current_user),
) -> ExecutionResponse:
    """폐기됨. 호환성 계약(v1) 때문에 경로와 요청 모양은 남기되, 이 경로로는 전환하지 않는다.

    손실 한도·초기 노출·재인증이 없는 전환은 J8 안전장치를 우회한다 -- /live-conversion을 쓴다.
    (소비자가 없음을 확인했다: 프런트엔드 0건. 107 §4 폐기 절차의 deprecated 단계.)
    """
    del execution_id, body, user  # OpenAPI 계약 모양(경로 파라미터·본문·인증)을 유지하려고 받는다
    raise HTTPException(
        status.HTTP_410_GONE,
        "convert-to-live는 폐기됐습니다. POST /executions/{id}/live-conversion을 사용하세요"
        "(손실 한도·초기 노출·재인증 필요).",
    )


@router.post("/{execution_id}/live-conversion", status_code=status.HTTP_201_CREATED)
async def live_conversion(
    execution_id: int,
    body: LiveConversionRequest,
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: ExecutionService = Depends(get_execution_service),
    readiness_service: LiveReadinessService = Depends(get_live_readiness_service),
) -> ExecutionResponse:
    # 자금이 실제로 배정되는 행동 -- 세션 탈취로는 못 하게
    # 비밀번호(MFA면 TOTP)를 다시 확인한다(J8 LP-3).
    await reauthenticate(auth, user, body.password, body.totp_code)
    # 사용자가 결정 시 본 근거를 감사 기록에 남긴다. 소유자·PAPER 검증도 여기서 같이 된다.
    readiness = await readiness_service.for_execution(execution_id, user.user_id)
    evidence = to_live_readiness_response(readiness).model_dump(mode="json")
    available = await _available_balance(resolver, user.user_id, body.exchange, body.currency)
    summary = await service.convert_to_live(
        user.user_id,
        execution_id,
        allocated_capital=body.allocated_capital,
        currency=body.currency,
        exchange=body.exchange,
        available_balance=available,
        max_loss_amount=body.max_loss_amount,
        initial_exposure_pct=body.initial_exposure_pct,
        readiness_evidence=evidence,
    )
    return to_execution_response(summary)
