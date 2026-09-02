"""LC-13 — 구매 홀드 흐름: `place_hold`(HOLD) → `capture_hold`(CAPTURE).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.5, §9 LC-13.

`purchase_service.py`가 예전에 하던 "buyer debit → seller credit →
commission credit" 3회 개별 포스팅을, `place_hold`(HOLD_PLACED)로 buyer
`AVAILABLE`을 `HELD`로 옮기고 `capture_hold`(HOLD_CAPTURED)로 그 `HELD`를
seller `PENDING_PAYOUT` + `PLATFORM:COMMISSION_REVENUE`로 나누는 한 쌍으로
교체한다. 둘 다 같은 `conn`(호출자가 이미 연 트랜잭션) 안에서 순서대로
호출되고, `ledger_hold` 행은 캡처 후에도 감사용으로 남는다(상태만
PENDING→CAPTURED로 전이, 삭제하지 않음).

동시성(task-424 decision): 같은 (purpose, reference)로 두 트랜잭션이
동시에 `place_hold`를 호출하면, 각자 서로 다른 `event_ref`(`hold:{hold_id}`,
매 호출 새 UUID)로 독립된 `HOLD_PLACED` 분개를 만들 수 있다 — 그래서
LC-9 `post_entry`의 멱등 재전송(REPLAY) 판정에 걸리지 않고 둘 다 실제로
buyer 잔액을 차감하려 시도한다(buyer 계정 `FOR UPDATE`로 순서만 직렬화).
이후 `holds.create()`의 `ledger_hold` `UNIQUE(purpose, reference)` 위반이
뒤늦게 하나만 남기고 나머지를 막는다 — 이 함수가 `asyncpg.UniqueViolationError`
를 잡아 `HoldConflictError`로 바꾸고, 호출자가 이를 잡지 않으면(권장 —
재시도 루프를 이 함수 안에 두지 않는다) 트랜잭션 전체가 롤백돼 그 시도의
분개(잔액 차감 포함)까지 함께 취소된다.

`release_hold`는 이 leaf 자체의 호출부는 없지만(구매는 즉시 capture),
LC-14(환불) 재사용을 위해 스펙 시그니처대로 구현해 둔다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.contracts.v1 import (
    HoldState,
    HoldView,
    JournalEntryView,
    LedgerEvent,
    LedgerEventType,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    allows_negative,
    parse_account_code,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.hold_state import HoldEvent
from src.foundation.ledger.domain.hold_state import transition as hold_fsm_transition
from src.foundation.ledger.domain.rounding import split_commission
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.hold_repository import HoldRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository


class HoldConflictError(Exception):
    """같은 (purpose, reference) 홀드가 이미 존재 — 동시 구매 요청 중 하나만
    통과하고 나머지는 이 예외로 거부된다(모듈 docstring 참고)."""


class CaptureResult(BaseModel):
    hold: HoldView
    entry: JournalEntryView
    commission_amount: Decimal
    payout_amount: Decimal


async def ensure_account(conn: asyncpg.Connection, account_code: str, currency: Currency) -> None:
    negative_ok = allows_negative(account_code)
    await conn.execute(
        "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (account_code) DO NOTHING",
        account_code, account_type(account_code).value, currency.value, negative_ok,
    )
    await conn.execute(
        "INSERT INTO ledger_balance (account_id, allow_negative) "
        "SELECT account_id, $2 FROM ledger_account WHERE account_code = $1 "
        "ON CONFLICT (account_id) DO NOTHING",
        account_code, negative_ok,
    )


async def _reconcile_available(
    conn: asyncpg.Connection,
    user_id: UUID,
    currency: Currency,
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
) -> None:
    """`user_wallets.balance`(레거시 투영)가 이 흐름 밖에서(테스트 픽스처의
    직접 SQL 등) 바뀐 drift를 원장에 흡수한다 —
    `legacy_wallet_bridge._reconcile_ledger_with_projection`과 동일 로직의
    사본이다(그 함수는 모듈 private이라 재사용 불가, 그 모듈 docstring의
    설명이 그대로 적용된다)."""
    code = ua(user_id, UserSub.AVAILABLE)
    await ensure_account(conn, code, currency)
    projected = await conn.fetchval(
        "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
    ) or Decimal("0")
    ledger_balance = await conn.fetchval(
        "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
        "ON la.account_id = lb.account_id WHERE la.account_code = $1",
        code,
    ) or Decimal("0")
    drift = projected - ledger_balance
    if drift == 0:
        return
    debit_account, credit_account = (
        (PLATFORM_CASH_CLEARING, code) if drift > 0 else (code, PLATFORM_CASH_CLEARING)
    )
    sync_event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"purchase_flow:legacy_sync:{user_id}:{uuid4()}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=abs(drift),
        currency=currency,
        parties={},
        extra={"debit_account": debit_account, "credit_account": credit_account},
    )
    await post_entry(conn, sync_event, journal=journal, balances=balances, audit=audit, clock=clock)


async def place_hold(
    conn: asyncpg.Connection,
    *,
    buyer_id: UUID,
    amount: Decimal,
    purpose: str,
    reference: str,
    expires_at: datetime,
    actor_subject_id: UUID | None,
    trace_id: UUID,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    holds: HoldRepository,
    currency: Currency = Currency.KRW,
    hold_id: UUID | None = None,
) -> HoldView:
    """buyer의 `AVAILABLE`을 `HELD`로 옮기고(HOLD_PLACED) `ledger_hold`에
    PENDING으로 기록한다. `(purpose, reference)`가 이미 존재하면
    `HoldConflictError`(모듈 docstring — 동시 구매 방어)."""
    resolved_hold_id = hold_id or uuid4()

    await _reconcile_available(
        conn, buyer_id, currency, journal=journal, balances=balances, audit=audit, clock=clock
    )
    await ensure_account(conn, ua(buyer_id, UserSub.HELD), currency)

    event = LedgerEvent(
        event_type=LedgerEventType.HOLD_PLACED,
        event_ref=f"hold:{resolved_hold_id}",
        tenant_id=None,
        actor_subject_id=actor_subject_id,
        trace_id=trace_id,
        amount=amount,
        currency=currency,
        parties={"buyer": buyer_id},
        extra={},
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )

    try:
        return await holds.create(
            conn,
            hold_id=resolved_hold_id,
            account_code=ua(buyer_id, UserSub.HELD),
            amount=amount,
            purpose=purpose,
            reference=reference,
            expires_at=expires_at,
            entry_id=entry.entry_id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HoldConflictError(
            f"이미 존재하는 홀드입니다: purpose={purpose!r} reference={reference!r}"
        ) from exc


async def capture_hold(
    conn: asyncpg.Connection,
    hold: HoldView,
    *,
    seller_id: UUID,
    commission_rate: Decimal,
    actor_subject_id: UUID | None,
    trace_id: UUID,
    now: datetime,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    holds: HoldRepository,
    currency: Currency = Currency.KRW,
) -> CaptureResult:
    """PENDING 홀드를 캡처한다: buyer `HELD` 전액을 seller `PENDING_PAYOUT`
    + `PLATFORM:COMMISSION_REVENUE`로 나눈다(HOLD_CAPTURED, §4.4). 만료된
    홀드는 `hold_state.py`(LC-5)의 `HoldExpiredError`로 fail-closed."""
    hold_fsm_transition(hold.state, HoldEvent.CAPTURE, now=now, expires_at=hold.expires_at)
    buyer_id = parse_account_code(hold.account_code).user_id
    assert buyer_id is not None  # hold.account_code는 항상 buyer의 USER:*:HELD 계정(place_hold)

    await ensure_account(conn, ua(seller_id, UserSub.PENDING_PAYOUT), currency)

    event = LedgerEvent(
        event_type=LedgerEventType.HOLD_CAPTURED,
        event_ref=f"hold:{hold.hold_id}:capture",
        tenant_id=None,
        actor_subject_id=actor_subject_id,
        trace_id=trace_id,
        amount=hold.amount,
        currency=currency,
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={"commission_rate": commission_rate},
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )

    updated_hold = await holds.transition(
        conn,
        hold.hold_id,
        expected_state=HoldState.PENDING,
        new_state=HoldState.CAPTURED,
        entry_id=entry.entry_id,
    )
    commission_amount, payout_amount = split_commission(hold.amount, commission_rate)
    return CaptureResult(
        hold=updated_hold,
        entry=entry,
        commission_amount=commission_amount,
        payout_amount=payout_amount,
    )


async def release_hold(
    conn: asyncpg.Connection,
    hold: HoldView,
    *,
    reason: str,
    actor_subject_id: UUID | None,
    trace_id: UUID,
    now: datetime,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    holds: HoldRepository,
    currency: Currency = Currency.KRW,
) -> HoldView:
    """PENDING 홀드를 buyer `AVAILABLE`로 되돌린다(HOLD_RELEASED). `reason`은
    호출자 감사 로그용 — `LedgerEvent.extra`엔 자리가 없어(§4.4 HOLD_RELEASED
    필수 extra 없음) 원장 사건 자체엔 반영하지 않는다."""
    hold_fsm_transition(hold.state, HoldEvent.RELEASE, now=now, expires_at=hold.expires_at)
    buyer_id = parse_account_code(hold.account_code).user_id
    assert buyer_id is not None

    event = LedgerEvent(
        event_type=LedgerEventType.HOLD_RELEASED,
        event_ref=f"hold:{hold.hold_id}:release",
        tenant_id=None,
        actor_subject_id=actor_subject_id,
        trace_id=trace_id,
        amount=hold.amount,
        currency=currency,
        parties={"buyer": buyer_id},
        extra={},
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
    return await holds.transition(
        conn,
        hold.hold_id,
        expected_state=HoldState.PENDING,
        new_state=HoldState.RELEASED,
        entry_id=entry.entry_id,
    )
