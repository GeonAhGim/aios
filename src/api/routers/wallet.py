"""FD-13.11(신설) — 사용자 지갑 조회 + 충전 요청 API.

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §1,
src/services/wallet_service.py 모듈 docstring. 충전 확인(관리자 액션)은
admin.py 라우터 소관 — 여기서는 사용자 본인의 조회/요청만 다룬다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user
from src.api.schemas.wallet import TopupRequestBody
from src.api.service_deps import get_wallet_service
from src.services.auth_service import User
from src.services.wallet_service import (
    WalletBalance,
    WalletService,
    WalletTopupError,
    WalletTopupRequest,
)

router = APIRouter()


@router.get("/balance")
async def get_balance(
    user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
) -> WalletBalance:
    return await service.get_balance(user.user_id)


@router.post("/topup-requests")
async def request_topup(
    body: TopupRequestBody,
    user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
) -> WalletTopupRequest:
    try:
        return await service.request_topup(user.user_id, body.amount)
    except WalletTopupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
