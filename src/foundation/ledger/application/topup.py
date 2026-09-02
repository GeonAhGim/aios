"""LC-12(b) — 충전 확인 → `TOPUP_CONFIRMED` 사건 포스팅.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5.4(3단계), §9 LC-12.

`wallet_service.confirm_topup`이 이 함수를 호출한다. `event_ref`를
`f"topup:{topup_id}"`로 고정해 `wallet_topup_requests.status`의 조건부
UPDATE(`WHERE status='PENDING'`)와 별개로 원장 레벨에서도 재확인이
REPLAY로 처리되게 한다(이중 방어 — 실제로는 상위 상태 체크가 먼저 막는다).
`journal`/`balances`/`audit`/`clock`은 호출자(`WalletService`)가 이미
`self._pool`로 만들어 둔 실제 포트를 그대로 받는다 — `legacy_wallet_bridge.py`
와 달리 이 계층은 `conn`만 받는 제약이 없다(모듈 docstring 참고, 신규
경로라 시그니처를 자유롭게 정할 수 있다)."""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    allows_negative,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository


async def _reconcile_ledger_with_projection(
    conn: asyncpg.Connection,
    user_id: UUID,
    currency: Currency,
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
) -> None:
    """`legacy_wallet_bridge._reconcile_ledger_with_projection`과 동일한
    drift 재동기화 — `user_wallets.balance`가 이 함수 밖에서(관리자 도구,
    테스트 픽스처) 바뀌어도 매 호출 전 원장을 그 값에 맞춘다(그 모듈
    docstring 참고, 여기선 요약만 남긴다)."""
    code = ua(user_id, UserSub.AVAILABLE)
    negative_ok = allows_negative(code)
    await conn.execute(
        "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (account_code) DO NOTHING",
        code, account_type(code).value, currency.value, negative_ok,
    )
    await conn.execute(
        "INSERT INTO ledger_balance (account_id, allow_negative) "
        "SELECT account_id, $2 FROM ledger_account WHERE account_code = $1 "
        "ON CONFLICT (account_id) DO NOTHING",
        code, negative_ok,
    )
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
        event_ref=f"bridge:legacy_sync:{user_id}:{uuid4()}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=abs(drift),
        currency=currency,
        parties={},
        extra={"debit_account": debit_account, "credit_account": credit_account},
    )
    await post_entry(conn, sync_event, journal=journal, balances=balances, audit=audit, clock=clock)


async def post_topup(
    conn: asyncpg.Connection,
    topup_id: int,
    user_id: UUID,
    amount: Decimal,
    admin_id: UUID,
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    currency: Currency = Currency.KRW,
) -> Decimal:
    """`post_entry`로 `TOPUP_CONFIRMED`를 포스팅한 뒤 `user_wallets.balance`·
    `wallet_transactions`(레거시 투영, §5.4 3단계)를 같은 트랜잭션에서 갱신하고
    갱신된 잔액을 반환한다."""
    await _reconcile_ledger_with_projection(
        conn, user_id, currency, journal=journal, balances=balances, audit=audit, clock=clock
    )
    event = LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=f"topup:{topup_id}",
        tenant_id=None,
        actor_subject_id=admin_id,
        trace_id=uuid4(),
        amount=amount,
        currency=currency,
        parties={"user": user_id},
        extra={},
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
    if entry.replayed:
        # 같은 topup_id로 재포스팅됐다 — REPLAY(원장 미변경)라 투영도 다시
        # 갱신하면 중복 적립이 된다. 현재 잔액을 그대로 돌려준다.
        current_balance: Decimal | None = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
        )
        return current_balance if current_balance is not None else Decimal("0")

    row = await conn.fetchrow(
        "UPDATE user_wallets SET balance = balance + $2, updated_at = now() "
        "WHERE user_id = $1 RETURNING balance",
        user_id, amount,
    )
    if row is None:
        row = await conn.fetchrow(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) RETURNING balance",
            user_id, amount,
        )
    await conn.execute(
        "INSERT INTO wallet_transactions "
        "(user_id, tx_type, amount, balance_after, related_purchase_id) "
        "VALUES ($1, 'TOPUP', $2, $3, NULL)",
        user_id, amount, row["balance"],
    )
    balance: Decimal = row["balance"]
    return balance
