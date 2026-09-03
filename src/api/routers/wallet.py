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

PLT-20 — raw HTTPException 제거(WalletTopupError는 이미 EXCEPTION_MAP에
VALIDATION_INVALID_FIELD로 매핑돼 있어 전역 핸들러가 그대로 처리한다).
task-1017 decision(PM 선반영) — 이 라우터는 금전 라우트라 PLT-15 멱등
헤더 규격(task-338/493)·프론트 배선(task-618/718)이 이미 붙어 있고, 성공
응답 봉투화는 mount_v1(PLT-16, src/api/versioning.py) 배선 이후 별도
리프에서 `/api/v1` 경로에만 적용하기로 미뤄졌다 — 그래서 available/held/
pending_payout 3분할 응답과 Idempotency-Key 처리를 그대로 두고 raw
HTTPException raise만 제거했다.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from src.api.deps import get_current_user, get_pool
from src.api.schemas.wallet import TopupRequestBody
from src.api.service_deps import get_wallet_service
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.application.queries import WalletBalanceView, get_balance
from src.services.auth_service import User
from src.services.wallet_service import WalletService, WalletTopupRequest

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
    return await service.request_topup(user.user_id, body.amount)
