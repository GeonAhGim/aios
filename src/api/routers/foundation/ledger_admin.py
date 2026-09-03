"""LC-15b — 관리자 정산 지급 확정 API.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 PAYOUT_PAID, §9 LC-15.

71번 §6 규칙: router는 auth/주입/transport validation/command invocation만
담당한다. `application/payouts.py::mark_payout_paid`(LC-15a, task-486)가
쓰기 경로 전체를 소유하므로 이 라우터는 두 번째 경로를 만들지 않고 그
함수를 그대로 호출한다 — `pool.acquire()` + 단일 트랜잭션 조립만 여기서
한다(`services/wallet_service.py::confirm_topup`과 동일 관례).

`UnknownPayoutBatchError`(존재하지 않는 batch_id)·`ConcurrencyConflictError`
(이미 `PAID`/`FAILED`인 배치 재확정 시도, 105번 표준 조건부 UPDATE 실패)는
여기서 try/except로 잡지 않고 전역 `EXCEPTION_MAP`(src/api/contracts/
exception_mapping.py, PLT 계약 task-108/112)에 위임한다 — 이 두 예외를
그 표에 추가하는 것도 이 리프의 일부다."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_admin, get_pool
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import PostgresPayoutRepository
from src.foundation.ledger.application.payouts import mark_payout_paid
from src.foundation.ledger.contracts.v1 import PayoutBatchView
from src.services.auth_service import User

router = APIRouter(prefix="/admin/ledger/payouts", tags=["foundation:ledger-admin"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarkPayoutPaidRequest(BaseModel):
    external_ref: str


@router.post("/{batch_id}/paid")
async def post_mark_payout_paid(
    batch_id: UUID,
    body: MarkPayoutPaidRequest,
    admin: User = Depends(get_current_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[PayoutBatchView]:
    async with pool.acquire() as conn, conn.transaction():
        result = await mark_payout_paid(
            conn,
            batch_id,
            admin_id=admin.user_id,
            external_ref=body.external_ref,
            journal=PostgresJournalRepository(pool),
            balances=PostgresBalanceRepository(pool),
            audit=PostgresAuditEventRepository(pool),
            clock=_utcnow,
            payouts=PostgresPayoutRepository(pool),
            trace_id=uuid4(),
        )
    return ok(result)
