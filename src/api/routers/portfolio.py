"""Router 19 — unified portfolio API (FD-19.1/FD-19.2).

Spec: functional_design_doc_v1.20.md#FD-19.1/FD-19.2, FD-3.2

PortfolioService assumes total_cash_balance is "already consolidated into a
single currency by the caller" (see services/portfolio_service.py module
docstring).  Phase 1 targets crypto (Bitget) only, so this router sums USDT
balances across all active exchanges the user has linked (FD-3.2).

PLT-19 (task-1016): removed all raw HTTPException usages — RebalanceError /
CapitalAllocationError are converted to the same 400 by the global handler
via EXCEPTION_MAP in exception_mapping.py.  Success response envelope
formatting is deferred for the same reason as PLT-17 decision (see
exchange_credentials.py module docstring).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.portfolio_deps import get_portfolio_service
from src.api.schemas.portfolio import RebalanceRequest, to_adjustments
from src.api.service_deps import get_credential_resolver, get_exchange_credential_service
from src.services.auth_service import User
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService
from src.services.portfolio_service import PortfolioService, PortfolioView, RebalanceResult

router = APIRouter()


async def _total_cash_balance(
    user_id: UUID,
    credential_service: ExchangeCredentialService,
    resolver: CredentialResolver,
) -> Decimal:
    summaries = await credential_service.list_for_user(user_id)
    total = Decimal("0")
    for summary in summaries:
        if not summary.is_active:
            continue
        try:
            adapter = await resolver.get_adapter(user_id, summary.exchange)
        except CredentialNotFoundError:
            continue
        for balance in await adapter.get_balance():
            if balance.asset.upper() == "USDT":
                total += balance.available
    return total


@router.get("")
async def get_portfolio(
    user: User = Depends(get_current_user),
    credential_service: ExchangeCredentialService = Depends(get_exchange_credential_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioView:
    total_cash_balance = await _total_cash_balance(user.user_id, credential_service, resolver)
    return await service.get_portfolio(user.user_id, total_cash_balance=total_cash_balance)


@router.post("/rebalance")
async def rebalance(
    body: RebalanceRequest,
    user: User = Depends(get_current_user),
    credential_service: ExchangeCredentialService = Depends(get_exchange_credential_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    service: PortfolioService = Depends(get_portfolio_service),
) -> RebalanceResult:
    total_cash_balance = await _total_cash_balance(user.user_id, credential_service, resolver)
    return await service.rebalance(
        user.user_id, to_adjustments(body), total_cash_balance=total_cash_balance
    )
