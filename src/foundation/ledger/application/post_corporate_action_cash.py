"""LA-25b — 현금배당의 원장 전기.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LA-25b,
ADR-2026-09-06-G §9 "현금배당이 원장에 도달하지 않는다(참조데이터만 기록)".

`record_corporate_action`(LA-14)은 CASH_DIVIDEND를 `md_corporate_action`
(참조데이터)에만 기록하고 끝난다 — 배당 현금이 원장(`ledger_journal_entry`)
에 없다. 이 함수가 그 간극을 닫는다: 이미 참조로 기록된
`CorporateAction`(action_type="CASH_DIVIDEND")과 보유자 1명당 확정된
지급액(`amount`)을 받아 `post_entry`(LC-9)로 단일 분개를 포스팅한다.
보유 수량 × 주당 배당액으로 `amount`를 산정하는 일(포지션 조회, I/O)은
이 리프 범위 밖이다 — DoD는 "amount>0인 CASH_DIVIDEND가 보유자 AVAILABLE에
전기"만 요구하고, 그 금액을 누가 얼마나 쥐고 있었는지는 별도 계층(포지션
읽기 모델)의 책임이다.

새 `LedgerEventType` 열거값(예: CASH_DIVIDEND)을 추가하려면
`ledger_journal_entry.event_type` CHECK 제약(LC-6 마이그레이션)을 ALTER하는
새 마이그레이션이 필요하다 — PM 승인(parent revision) 없이 이 리프에서
마이그레이션을 만들지 않는다(헤드리스 워커 프로토콜, `chargeback.py` 모듈
docstring과 동일 판단). 대신 이미 존재하는 `MANUAL_ADJUSTMENT`(LC-4,
"명시된 행" 경로)를 재사용해 `PLATFORM:CASH_CLEARING` → `USER:{holder}:
AVAILABLE`로 포스팅한다 — `ledger_journal_entry.event_type` 컬럼값은
MANUAL_ADJUSTMENT로 남지만, `event_ref`를
`"corp_action_cash:{instrument_id}:{ex_date}:{holder_id}"`로 고정해
`idempotency_key`(`{event_type}:{event_ref}`)가 정확히 (instrument, ex_date,
holder) 위에서 유일해지고, 감사 payload(`event_ref`)로 원인을 추적할 수 있다.

`amount <= 0`이면 아예 포스팅하지 않고 `None`을 반환한다 — DoD가 요구하는
것은 "amount>0인 CASH_DIVIDEND"의 전기이지, 0 이하 금액을 분개로 만들
근거가 없다(`PostingLine.amount`도 항상 `> 0`이어야 함, §3.3).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.application.purchase_flow import ensure_account
from src.foundation.ledger.contracts.v1 import (
    JournalEntryView,
    LedgerEvent,
    LedgerEventType,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository
from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["UnsupportedCorporateActionError", "post_corporate_action_cash"]


class UnsupportedCorporateActionError(ValueError):
    """`action.action_type`이 "CASH_DIVIDEND"가 아니다 — 이 함수는 현금배당
    전용이다(분할·병합·역병합은 다른 도메인의 책임, §9 BT-20의 백테스트
    조정과 LA-8 `domain/corporate_actions/adjustment.py`가 각각 다룬다)."""

    def __init__(self, action_type: str) -> None:
        super().__init__(f"CASH_DIVIDEND 전용 함수: action_type={action_type!r}")
        self.action_type = action_type


def _event_ref(action: CorporateAction, holder_id: UUID) -> str:
    """`(instrument, ex_date, holder)` 3중 위에서 유일한 참조. `MANUAL_ADJUSTMENT`
    와 조합된 `idempotency_key`(`{event_type}:{event_ref}`)가 이 값으로 정확히
    그 3중 멱등을 만든다."""
    return f"corp_action_cash:{action.instrument_id}:{action.ex_date.isoformat()}:{holder_id}"


async def post_corporate_action_cash(
    conn: asyncpg.Connection,
    action: CorporateAction,
    *,
    holder_id: UUID,
    amount: Decimal,
    admin_id: UUID | None,
    trace_id: UUID,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    currency: Currency = Currency.KRW,
) -> JournalEntryView | None:
    """CASH_DIVIDEND 참조데이터 `action`에 대해 보유자 `holder_id`의 확정
    지급액 `amount`를 그 `AVAILABLE` 계정에 전기한다. `amount <= 0`이면
    포스팅하지 않고 `None`을 반환한다. 재호출(같은 instrument/ex_date/holder,
    같은 amount)은 `post_entry`(LC-9)의 REPLAY로 새 분개를 만들지 않는다 —
    금액이 달라진 재호출은 `IdempotencyDigestMismatchError`(409, 재시도 불가)."""
    if action.action_type != "CASH_DIVIDEND":
        raise UnsupportedCorporateActionError(action.action_type)
    if amount <= 0:
        return None

    holder_available = ua(holder_id, UserSub.AVAILABLE)
    await ensure_account(conn, holder_available, currency)

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=_event_ref(action, holder_id),
        tenant_id=None,
        actor_subject_id=admin_id,
        trace_id=trace_id,
        amount=amount,
        currency=currency,
        parties={"holder": holder_id},
        extra={"debit_account": PLATFORM_CASH_CLEARING, "credit_account": holder_available},
    )
    return await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
