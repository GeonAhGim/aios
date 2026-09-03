"""LC-15a — 정산 배치 생성(`PAYOUT_RELEASE`)·오프플랫폼 지급 확정(`PAYOUT_PAID`).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LC-15.

`domain/payout_schedule.py`(LC-5)가 "무엇을 얼마나 묶을지"를 순수하게
계산하고, 이 모듈은 그 결과를 `post_entry`(LC-9)로 포스팅한 뒤
`PayoutRepository`(이 리프의 `adapters/postgres_payout_repository.py`)에
저장하는 조립만 한다 — 창 길이·전이 규칙을 재구현하지 않는다.

정산창 7일(§10 R2, ADR 개정 전 Draft)은 상수로 코드에 박지 않고
`DEFAULT_SETTLEMENT_WINDOW`(호출자가 override 가능한 기본값)로 둔다 —
`application/scheduler.py`의 `DEFAULT_INTERVAL_SECONDS`와 같은 관례.

멱등성: `schedule_payouts`가 만드는 각 `PAYOUT_RELEASE` 분개의 `event_ref`는
`f"payout_batch:{item.batch_key}"`(도메인 `batch_key`, `(seller_user_id,
period_end)`와 동형)다. 같은 캡처 목록·기간으로 재호출해도 `post_entry`가
REPLAY로 받고 `PayoutRepository.create_batch`도 기존 배치를 그대로
반환하므로(어댑터 docstring), 판매자 잔액이 중복 이동하지 않는다.

`conn`은 호출자가 이미 연 트랜잭션이며 두 함수 모두 스스로 커밋/롤백을
결정하지 않는다(`post_entry`와 동일 계약).

`post_entry`는 미지 계정을 조용히 만들지 않는다(fail-closed) — 캡처
시점(`purchase_flow.capture_hold`)엔 판매자 `PENDING_PAYOUT`만 만들어지고
`AVAILABLE`은 아직 없을 수 있어(첫 정산), `schedule_payouts`가 포스팅 전에
`purchase_flow.ensure_account`(모듈 private 아님, 그대로 재사용)로 만들어
둔다.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.application.purchase_flow import ensure_account
from src.foundation.ledger.contracts.v1 import (
    LedgerEvent,
    LedgerEventType,
    PayoutBatchView,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.payout_schedule import CaptureRecord
from src.foundation.ledger.domain.payout_schedule import schedule_payouts as _schedule_payout_items
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository
from src.foundation.ledger.ports.payout_repository import PayoutRepository

DEFAULT_SETTLEMENT_WINDOW = timedelta(days=7)


class UnknownPayoutBatchError(Exception):
    """`mark_payout_paid`에 주어진 `batch_id`가 `ledger_payout_batch`에 없다."""

    def __init__(self, batch_id: UUID) -> None:
        super().__init__(f"알 수 없는 payout batch_id: {batch_id}")
        self.batch_id = batch_id


async def schedule_payouts(
    conn: asyncpg.Connection,
    captures: Sequence[CaptureRecord],
    *,
    period_start: date,
    period_end: date,
    now: datetime,
    actor_subject_id: UUID | None,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    payouts: PayoutRepository,
    settlement_window: timedelta = DEFAULT_SETTLEMENT_WINDOW,
    trace_id: UUID | None = None,
) -> list[PayoutBatchView]:
    """`captures`(정산 미완료 `HOLD_CAPTURED` 캡처 후보 — 조회는 호출자 책임)
    중 홀드 창(`settlement_window`)이 지난 것만 판매자별로 묶어
    `PAYOUT_RELEASE`(판매자 `PENDING_PAYOUT` → `AVAILABLE`)를 포스팅하고
    `ledger_payout_batch`/`ledger_payout_item`에 기록한다. 창 미경과 캡처는
    조용히 제외된다(`domain/payout_schedule.schedule_payouts` 계약 그대로 —
    다음 실행에서 다시 후보가 된다)."""
    items = _schedule_payout_items(
        captures,
        period_start=period_start,
        period_end=period_end,
        settlement_window=settlement_window,
        now=now,
    )

    batches: list[PayoutBatchView] = []
    for item in items:
        await ensure_account(conn, ua(item.seller_user_id, UserSub.AVAILABLE), item.currency)
        event = LedgerEvent(
            event_type=LedgerEventType.PAYOUT_RELEASE,
            event_ref=f"payout_batch:{item.batch_key}",
            tenant_id=None,
            actor_subject_id=actor_subject_id,
            trace_id=trace_id or uuid4(),
            amount=item.amount,
            currency=item.currency,
            parties={"seller": item.seller_user_id},
            extra={},
        )
        entry = await post_entry(
            conn, event, journal=journal, balances=balances, audit=audit, clock=clock
        )
        batch = await payouts.create_batch(
            conn,
            batch_id=uuid4(),
            seller_user_id=item.seller_user_id,
            period_start=item.period_start,
            period_end=item.period_end,
            amount=item.amount,
            capture_entry_ids=item.capture_entry_ids,
            release_entry_id=entry.entry_id,
        )
        batches.append(batch)
    return batches


async def mark_payout_paid(
    conn: asyncpg.Connection,
    batch_id: UUID,
    *,
    admin_id: UUID,
    external_ref: str,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    payouts: PayoutRepository,
    trace_id: UUID | None = None,
) -> PayoutBatchView:
    """`RELEASED` 배치를 오프플랫폼 송금 확정(`PAYOUT_PAID`)한다: 판매자
    `AVAILABLE` → `PLATFORM:PAYOUT_CLEARING`(§4.4). 배치가 없으면
    `UnknownPayoutBatchError`, 이미 `PAID`/`FAILED`면 저장소가
    `ConcurrencyConflictError`를 던진다(105번 표준, 조건부 전이)."""
    row = await conn.fetchrow(
        "SELECT seller_user_id, amount, currency FROM ledger_payout_batch WHERE batch_id = $1",
        batch_id,
    )
    if row is None:
        raise UnknownPayoutBatchError(batch_id)

    event = LedgerEvent(
        event_type=LedgerEventType.PAYOUT_PAID,
        event_ref=f"payout_batch:{batch_id}:paid",
        tenant_id=None,
        actor_subject_id=admin_id,
        trace_id=trace_id or uuid4(),
        amount=row["amount"],
        currency=Currency(row["currency"]),
        parties={"seller": row["seller_user_id"]},
        extra={"external_ref": external_ref},
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
    return await payouts.mark_paid(
        conn, batch_id, paid_entry_id=entry.entry_id, external_ref=external_ref
    )
