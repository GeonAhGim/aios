"""LC-14 — 환불 재원 결정(R1/R2/R3) + `REFUND` 포스팅.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 REFUND, §9 LC-14.

감사 §1.1 C2 최종 해소: `posting_rules._refund`(LC-4, 이미 구현·불변)는
buyer `AVAILABLE` 적립과 **같은 분개** 안에서 seller의 판매대금(payout)과
`PLATFORM:COMMISSION_REVENUE`를 반드시 환수한다 — 그래서 환불 1건이
시스템 총잔액을 늘리는 경로가 애초에 존재하지 않는다. 이 리프의 책임은
그 분개가 요구하는 `refund_case`("R1"|"R2"|"R3", §4.4 표)를 판매자의
실제 잔액 상태를 보고 **결정**하는 것뿐이다(순수 규칙은 `posting_rules`가,
I/O가 필요한 케이스 선택은 여기가 한다).

케이스 판정(§4.4 표):
- R1: 판매대금이 아직 seller `PENDING_PAYOUT`에 있다(정산 창 내) — 그 계정에서
  직접 차변.
- R2: 정산 창 경과 후(`PAYOUT_RELEASE`로 이미 `AVAILABLE`로 이동) seller
  `AVAILABLE` ≥ payout.
- R3: 창 경과 후 seller `AVAILABLE` < payout — 가용분은 `AVAILABLE`, 부족분은
  `RECEIVABLE`(유일 음수허용 계정, 대손 이연)로 나눈다.

두 계정(PENDING_PAYOUT/AVAILABLE) 모두 `balances.get_for_update`로 조회해
같은 트랜잭션 안에서 잠그고 판정한다 — `post_entry` 자신도 분개에 실제 쓰인
계정들을 다시 `FOR UPDATE`로 잠그므로(post_entry.py 참고), 이 판정과 그
잠금 사이에 값이 바뀌지 않는다(둘 다 같은 `conn`, 같은 트랜잭션).

멱등: `event_ref`가 `purchase_id`에만 매인 안정 키(`refund:purchase:{id}`)라
같은 구매를 두 번 환불 요청해도 `post_entry`(LC-9)가 REPLAY(분개 재작성 0행)
또는(그 사이 잔액이 바뀌어 케이스가 달라졌다면) DIGEST_MISMATCH(409, DENIED
감사)로 거부한다 — 이 함수는 재시도 루프를 두지 않는다(호출자 책임).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.application.purchase_flow import ensure_account
from src.foundation.ledger.contracts.v1 import (
    JournalEntryView,
    LedgerEvent,
    LedgerEventType,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.rounding import split_commission
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository


class RefundView(BaseModel):
    entry: JournalEntryView
    refund_case: str
    payout_amount: Decimal
    commission_amount: Decimal


async def _account_balance(
    conn: asyncpg.Connection, balances: BalanceRepository, account_code: str
) -> Decimal:
    current = await balances.get_for_update(conn, [account_code])
    return current[account_code].balance


async def _resolve_refund_case(
    conn: asyncpg.Connection,
    balances: BalanceRepository,
    *,
    seller_id: UUID,
    payout: Decimal,
) -> tuple[str, Decimal | None]:
    """§4.4 R1/R2/R3 판정. R3일 때만 두 번째 값(seller `AVAILABLE` 잔액)을
    채운다 — `posting_rules._refund`의 `seller_available_amount` extra 키."""
    pending_payout = await _account_balance(conn, balances, ua(seller_id, UserSub.PENDING_PAYOUT))
    if pending_payout >= payout:
        return "R1", None

    available = await _account_balance(conn, balances, ua(seller_id, UserSub.AVAILABLE))
    if available >= payout:
        return "R2", None
    return "R3", available


async def post_refund(
    conn: asyncpg.Connection,
    *,
    purchase_id: int,
    buyer_id: UUID,
    seller_id: UUID,
    price: Decimal,
    commission_rate: Decimal,
    admin_id: UUID | None,
    trace_id: UUID,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    currency: Currency = Currency.KRW,
) -> RefundView:
    """환불 재원(R1/R2/R3)을 판정하고 단일 `REFUND` 분개를 포스팅한다."""
    commission_amount, payout_amount = split_commission(price, commission_rate)

    for account in (
        ua(seller_id, UserSub.PENDING_PAYOUT),
        ua(seller_id, UserSub.AVAILABLE),
        ua(seller_id, UserSub.RECEIVABLE),
        ua(buyer_id, UserSub.AVAILABLE),
    ):
        await ensure_account(conn, account, currency)

    refund_case, seller_available = await _resolve_refund_case(
        conn, balances, seller_id=seller_id, payout=payout_amount
    )

    extra: dict[str, Decimal | str] = {
        "commission_rate": commission_rate,
        "refund_case": refund_case,
    }
    if seller_available is not None:
        extra["seller_available_amount"] = seller_available

    event = LedgerEvent(
        event_type=LedgerEventType.REFUND,
        event_ref=f"refund:purchase:{purchase_id}",
        tenant_id=None,
        actor_subject_id=admin_id,
        trace_id=trace_id,
        amount=price,
        currency=currency,
        parties={"buyer": buyer_id, "seller": seller_id},
        extra=extra,
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
    return RefundView(
        entry=entry,
        refund_case=refund_case,
        payout_amount=payout_amount,
        commission_amount=commission_amount,
    )
