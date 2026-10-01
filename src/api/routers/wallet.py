"""FD-13.11 (new) — user wallet balance lookup + topup request API.

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §1,
src/services/wallet_service.py module docstring. Topup confirmation (an admin
action) belongs to the admin.py router — this module only covers the user's
own balance lookup/request.

LC-16 — `/balance` now calls `application/queries.py::get_balance` (LC-16).
Rule §6 of task-71 still applies (router only does auth/injection/transport
validation) — all SQL/balance comparison logic lives in that module (same
practice as `ledger_admin.py`: only `pool.acquire()` + adapter assembly happen
here). `WalletService.get_balance` (the legacy single-`balance` lookup) is no
longer used by this router, but it stays as-is since it is a public service
method outside this leaf's file list.

PLT-20 — removed raw HTTPException (WalletTopupError is already mapped to
VALIDATION_INVALID_FIELD in EXCEPTION_MAP, so the global handler already
handles it). Per task-1017 decision (pre-reflected by PM), this router is a
money route where the PLT-15 idempotency header spec (task-338/493) and the
frontend wiring (task-618/718) are already in place, and success-response
envelope wrapping was deferred to a separate leaf to apply only to `/api/v1`
paths after the mount_v1 (PLT-16, src/api/versioning.py) wiring — so the
available/held/pending_payout 3-way split response and Idempotency-Key
handling were left as-is, and only the raw HTTPException raise was removed.
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
