"""LC-8a — 원장 정산 배치 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.4, §5, §9 LC-8.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_payout_repository.py,
LC-15/task 후속)은 모른다(71번 §4). 배치를 언제·얼마나 묶을지는
`domain/payout_schedule.py`(LC-5)의 순수 계산 책임이고, 이 포트는 그 결과를
저장·조회만 한다.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.ledger.contracts.v1 import PayoutBatchView


class PayoutRepository(Protocol):
    async def create_batch(
        self,
        conn: asyncpg.Connection,
        *,
        batch_id: UUID,
        seller_user_id: UUID,
        period_start: date,
        period_end: date,
        amount: Decimal,
        capture_entry_ids: Sequence[UUID],
        release_entry_id: UUID | None,
    ) -> PayoutBatchView:
        """§5 C: `INSERT ... ON CONFLICT (seller_user_id, period_end) DO NOTHING`
        — 같은 (판매자, 기간) 재호출은 새로 만들지 않고 기존 배치를 그대로
        반환한다(멱등). `capture_entry_ids`는 각각 `ledger_payout_item`에
        `UNIQUE(capture_entry_id)`로 기록되어 같은 캡처가 두 배치에 들어가는
        것을 막는다. `release_entry_id`가 주어지면(PAYOUT_RELEASE 분개가 같은
        트랜잭션에서 이미 포스팅됨) 상태는 `RELEASED`, `None`이면
        `SCHEDULED`."""
        ...

    async def list_due(self, conn: asyncpg.Connection) -> list[PayoutBatchView]:
        """상태가 `RELEASED`(오프플랫폼 송금 대기)인 배치 전체 — 관리자 지급
        큐(`POST /admin/ledger/payouts/{id}/paid`)가 조회한다."""
        ...

    async def mark_paid(
        self,
        conn: asyncpg.Connection,
        batch_id: UUID,
        *,
        paid_entry_id: UUID,
        external_ref: str,
    ) -> PayoutBatchView:
        """`RELEASED → PAID` 조건부 전이(§4.4 PAYOUT_PAID). 이미 `PAID`이거나
        `FAILED`면 `ConcurrencyConflictError`."""
        ...
