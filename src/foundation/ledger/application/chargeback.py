"""LC-15b — CHARGEBACK(입금 취소) 포스팅: RECEIVABLE 상계.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 CHARGEBACK, §9 LC-15.

`domain/posting_rules.py::_chargeback`(이미 구현·불변)는 사용자 `AVAILABLE`
잔액을 `user_available_amount`(extra)로 넘겨받아 가용분/부족분을
`_split_by_available`로 나눈다 — 가용분은 `AVAILABLE`에서, 부족분은
`RECEIVABLE`(유일 음수허용 계정)에서 차변한다. 이 모듈의 책임은 그 extra
값을 실제 잔액 조회(I/O)로 채우는 것뿐이다(`refund.py`의
`_resolve_refund_case`와 같은 역할 분담 — 순수 판정은 posting_rules,
잔액 조회는 여기).

지급(PAYOUT_PAID) 이후에도 차지백이 걸릴 수 있다 — 판매대금이 이미
판매자 AVAILABLE에서 PLATFORM:PAYOUT_CLEARING으로 빠져나간 뒤 원 입금
자체가 취소되면(카드사 차지백 등), 그 사용자 AVAILABLE만으로 amount를
감당 못 하는 경우가 흔하다. 그 부족분은 RECEIVABLE 음수 잔액으로
이연되어 분개 단위 Σ차변=Σ대변을 그대로 유지한다(REFUND R3와 동일한
대손 이연 패턴 — `balance_rules.check_balanced`가 강제).

`USER:*:AVAILABLE`은 LC-6 시드 대상이 아니라(사용자별 계정, PLATFORM:*
와 달리 최초 사용 시점에 만들어짐) `ensure_account`로 직접 보장한다.
`PLATFORM:CASH_CLEARING`은 LC-6 마이그레이션이 미리 시드해두므로(
`purchase_flow.ensure_account` 호출 목록에 없는 이유와 동일) 여기서도
따로 만들지 않는다.

멱등: `event_ref`는 `chargeback:topup:{topup_id}`(topup id에만 매인
안정 키)다. 같은 차지백을 두 번 요청해도 `post_entry`가 REPLAY(그 사이
잔액이 바뀌지 않았다면) 또는 DIGEST_MISMATCH(409, 잔액이 바뀌어 분할이
달라졌다면)로 거부한다 — 이 함수는 재시도 루프를 두지 않는다(호출자
책임, refund.py와 동일 계약)."""
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
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository


class ChargebackView(BaseModel):
    entry: JournalEntryView
    user_available_amount: Decimal


async def post_chargeback(
    conn: asyncpg.Connection,
    *,
    topup_id: int,
    user_id: UUID,
    amount: Decimal,
    admin_id: UUID | None,
    trace_id: UUID,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    currency: Currency = Currency.KRW,
) -> ChargebackView:
    """입금 취소(§4.4 CHARGEBACK)를 사용자 `AVAILABLE` 잔액 대비 판정해
    단일 `CHARGEBACK` 분개를 포스팅한다. `amount`를 넘는 부족분은
    `RECEIVABLE` 음수 잔액으로 이연된다(`posting_rules._chargeback`이
    실제 분할을 수행 — 여기서는 그 입력값만 조회해 넘긴다)."""
    available_account = ua(user_id, UserSub.AVAILABLE)
    receivable_account = ua(user_id, UserSub.RECEIVABLE)
    await ensure_account(conn, available_account, currency)
    await ensure_account(conn, receivable_account, currency)

    current = await balances.get_for_update(conn, [available_account])
    available = current[available_account].balance

    event = LedgerEvent(
        event_type=LedgerEventType.CHARGEBACK,
        event_ref=f"chargeback:topup:{topup_id}",
        tenant_id=None,
        actor_subject_id=admin_id,
        trace_id=trace_id,
        amount=amount,
        currency=currency,
        parties={"user": user_id},
        extra={"user_available_amount": available},
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
    return ChargebackView(entry=entry, user_available_amount=available)
