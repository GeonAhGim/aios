"""19번 — 통합 포트폴리오 API 라우터 (FD-19.1/FD-19.2).

Spec: 기능설계문서_v1.20.md#FD-19.1/FD-19.2, FD-3.2

PortfolioService는 total_cash_balance를 "호출부가 이미 단일 통화로
정리해 전달한다"고 가정한다(services/portfolio_service.py 모듈 docstring
참조) — Phase 1이 crypto(Bitget) 단일 자산군 전제이므로, 사용자가 연동한
모든 활성 거래소의 USDT 잔고(FD-3.2)를 합산해 넘긴다.

PLT-19(task-1016): raw HTTPException을 전부 제거했다 — RebalanceError/
CapitalAllocationError는 전역 핸들러가 exception_mapping.py의
EXCEPTION_MAP을 통해 동일한 400으로 변환한다. 이 라우터의 성공 응답
봉투화는 PLT-17 decision과 동일 사유로 보류한다(exchange_credentials.py
모듈 docstring 참조).
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
