"""Reconciliation & Resilience API — Rule §6 (71): router handles auth, injection,
transport validation, and command invocation only.

Domain exceptions are not caught here — `EXCEPTION_MAP` in
`src/api/contracts/exception_mapping.py` wraps and translates them in the
global handler (§9 PLT-21b decision, task-1217).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.api.foundation_deps import (
    get_connection_repository,
    get_reconciliation_repository,
    get_risk_gate_repository,
)
from src.api.schemas.foundation.reconciliation import (
    ReconciliationRunView,
    ReconciliationStateListResponse,
    ReconciliationStateView,
    ResolveReconciliationRequest,
    RunReconciliationRequest,
)
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.reconciliation.application.resolve_reconciliation import (
    resolve_reconciliation,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from src.foundation.reconciliation.ports.repository import ReconciliationRepository
from src.foundation.reconciliation.projections import build_reconciliation_state_list_view
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/reconciliation", tags=["foundation:reconciliation"])


@router.get("")
async def list_reconciliation_states(
    user: User = Depends(get_current_user),
    repo: ReconciliationRepository = Depends(get_reconciliation_repository),
) -> ApiResponse[ReconciliationStateListResponse]:
    view = await build_reconciliation_state_list_view(repo, user.user_id)
    return ok(ReconciliationStateListResponse(states=view.states, as_of=view.as_of))


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def post_run_reconciliation(
    body: RunReconciliationRequest,
    user: User = Depends(get_current_user),
    repo: ReconciliationRepository = Depends(get_reconciliation_repository),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
    risk_repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> ApiResponse[ReconciliationRunView]:
    result = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=user.user_id,
        target_type=body.target_type,
        target_ref=body.target_ref,
        connection_id=body.connection_id,
        entities=body.entities,
    )
    return ok(result)


@router.post("/{target_ref}:resolve")
async def post_resolve_reconciliation(
    target_ref: UUID,
    body: ResolveReconciliationRequest,
    user: User = Depends(get_current_user),
    repo: ReconciliationRepository = Depends(get_reconciliation_repository),
) -> ApiResponse[ReconciliationStateView]:
    result = await resolve_reconciliation(
        repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        target_ref=target_ref,
        reason=body.reason,
    )
    return ok(result)
