"""16번 — 실행 제어판 API 라우터 (FD-16.1/16.2/16.3/16.4/16.6).

Spec: 기능설계문서_v1.20.md#FD-16.1~FD-16.4/FD-16.6

FD-16.1 처리단계 ①"사용자 계좌 잔고(FD-3.2) 대비 배분 가능 여부 확인"에
따라 available_balance를 클라이언트가 보내지 않고 CredentialResolver로
실제 거래소 잔고(FD-3.2)를 조회해 서버가 직접 계산한다.

편차: LIVE 실행의 승인(FD-10.1 패턴 재사용)을 실제로 승인/거절하는
HTTP 엔드포인트는 이 leaf에 없다 — 승인 결정은 관리자 액션이라
18번(관리자 도구) 스콥이며, 여기서는 승인 대기 상태를 정직하게
노출(approval_request_id, PENDING_APPROVAL)만 한다.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user
from src.api.execution_deps import get_execution_monitoring_service, get_execution_service
from src.api.schemas.execution import (
    ConvertToLiveRequest,
    ExecutionCardResponse,
    ExecutionCreateRequest,
    ExecutionResponse,
    RetireRequest,
    SetMaxDrawdownRequest,
    to_execution_card_response,
    to_execution_response,
)
from src.api.service_deps import get_credential_resolver
from src.services.auth_service import User
from src.services.capital_allocation import CapitalAllocationError
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import (
    ExecutionControlError,
    ExecutionCreateError,
    ExecutionService,
)

router = APIRouter()


async def _available_balance(
    resolver: CredentialResolver, user_id: UUID, exchange: str, currency: str
) -> Decimal:
    try:
        adapter = await resolver.get_adapter(user_id, exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
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
    try:
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
    except (ExecutionCreateError, CapitalAllocationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_execution_response(summary)


@router.get("")
async def list_executions(
    user: User = Depends(get_current_user),
    service: ExecutionMonitoringService = Depends(get_execution_monitoring_service),
) -> list[ExecutionCardResponse]:
    cards = await service.list_for_user(user.user_id)
    return [to_execution_card_response(card) for card in cards]


@router.post("/{execution_id}/start")
async def start_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    try:
        summary = await service.start(execution_id, user.user_id)
    except ExecutionControlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_execution_response(summary)


@router.post("/{execution_id}/pause")
async def pause_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    try:
        summary = await service.pause(execution_id, paused_by="USER", user_id=user.user_id)
    except ExecutionControlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_execution_response(summary)


@router.patch("/{execution_id}/risk-guard")
async def set_execution_risk_guard(
    execution_id: int,
    body: SetMaxDrawdownRequest,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    try:
        summary = await service.set_max_drawdown(
            execution_id, user.user_id, body.max_drawdown_pct
        )
    except ExecutionControlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_execution_response(summary)


@router.post("/{execution_id}/retire")
async def retire_execution(
    execution_id: int,
    body: RetireRequest,
    user: User = Depends(get_current_user),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    try:
        summary = await service.retire(
            execution_id, user.user_id, liquidation=body.liquidation
        )
    except ExecutionControlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_execution_response(summary)


@router.post("/{execution_id}/convert-to-live", status_code=status.HTTP_201_CREATED)
async def convert_to_live(
    execution_id: int,
    body: ConvertToLiveRequest,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    available = await _available_balance(resolver, user.user_id, body.exchange, body.currency)
    try:
        summary = await service.convert_to_live(
            user.user_id,
            execution_id,
            allocated_capital=body.allocated_capital,
            currency=body.currency,
            exchange=body.exchange,
            available_balance=available,
        )
    except (ExecutionControlError, ExecutionCreateError, CapitalAllocationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_execution_response(summary)
