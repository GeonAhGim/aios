"""FD-13.11(신설) — 사용자 지갑 조회 + 충전 요청 API.

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §1,
src/services/wallet_service.py 모듈 docstring. 충전 확인(관리자 액션)은
admin.py 라우터 소관 — 여기서는 사용자 본인의 조회/요청만 다룬다.

LC-16 — `/balance`는 이제 `application/queries.py::get_balance`(LC-16)를
호출한다. 71번 §6 규칙(router는 auth/주입/transport validation만) 그대로 —
SQL·잔액 비교 로직은 전부 그 모듈에 있다(`ledger_admin.py`와 동일 관행으로
`pool.acquire()` + 어댑터 조립만 여기서 한다). `WalletService.get_balance`
(레거시 단일 `balance` 조회)는 이제 이 라우터가 쓰지 않지만, 공개 서비스
메서드라 이 리프의 파일 목록 밖이라 그대로 둔다.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user, get_pool
from src.api.schemas.wallet import TopupRequestBody
from src.api.service_deps import get_wallet_service
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.application.queries import WalletBalanceView, get_balance
from src.services.auth_service import User
from src.services.wallet_service import WalletService, WalletTopupError, WalletTopupRequest

router = APIRouter()


@router.get("/balance")
async def get_wallet_balance(
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> WalletBalanceView:
    return await get_balance(pool, user.user_id, balances=PostgresBalanceRepository(pool))


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
