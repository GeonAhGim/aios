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

from fastapi import APIRouter, Depends, status

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
from src.services.credential_resolver import CredentialResolver
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService

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


@router.post("/{execution_id}/convert-to-live", status_code=status.HTTP_201_CREATED)
async def convert_to_live(
    execution_id: int,
    body: ConvertToLiveRequest,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    available = await _available_balance(resolver, user.user_id, body.exchange, body.currency)
    summary = await service.convert_to_live(
        user.user_id,
        execution_id,
        allocated_capital=body.allocated_capital,
        currency=body.currency,
        exchange=body.exchange,
        available_balance=available,
    )
    return to_execution_response(summary)
