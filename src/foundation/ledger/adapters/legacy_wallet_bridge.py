"""LC-12(a) — `wallet_service.debit/credit` 본문을 `post_entry`(LC-9)로 대체하는
전환기 브리지.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5.4(3단계), §9 LC-12.

`purchase_service.py`/`dispute_resolution_service.py`는 LC-13/LC-14까지
`wallet_service.debit/credit`를 그대로 호출한다(이 리프에서 그 호출부는
바꾸지 않는다) — 그래서 `bridge_debit/bridge_credit`도 `conn`만 받는 기존
시그니처를 유지해야 하고, `post_entry`가 요구하는 `journal`/`balances`/
`audit` 포트를 그 호출부로부터 주입받을 방법이 없다(`pool`을 쥔 쪽은
purchase_service 등이지 여기가 아니다). 세 어댑터 모두 `__init__`은
`self._pool = pool`(및 `PostgresJournalRepository`는 그걸로 감사 리포를
하나 더 만드는 것) 뿐이고, 이 리프가 실제로 쓰는 메서드
(`get_for_update`/`apply`/`append`/`find_by_idempotency_key`/
`append_event_in`)는 전부 `conn` 인자로만 동작하며 `self._pool`을 절대
참조하지 않는다(소스 확인, `adapters/postgres_*.py`) — 그래서 `pool` 자리에
placeholder를 넣어도 안전하다. 이 전제가 깨지면(위 클래스들이 리팩터되어
`self._pool`을 실제로 쓰게 되면) 여기도 진짜 pool로 바꿔야 한다.

일반 사건(TOPUP이 아닌 모든 `tx_type`)은 `MANUAL_ADJUSTMENT`로 포스팅한다
— `USER:{user}:AVAILABLE` ↔ `PLATFORM:CASH_CLEARING` 페어. 이 카운터
계정은 `TOPUP_CONFIRMED`도 같은 계정을 쓰므로(`posting_rules._topup_confirmed`),
모든 브리지 호출에서 CASH_CLEARING 잔액은 항상 "전 사용자 AVAILABLE 잔액의
합"과 정확히 같게 움직인다(각 브리지 호출이 사용자 계정에 준 델타를
CASH_CLEARING에 부호까지 그대로 미러링) — 개별 사용자 잔액이 항상 ≥0이므로
그 합도 ≥0, 따라서 CASH_CLEARING(`allow_negative=False`, LC-6 시드)이
이 경로만으로는 절대 음수로 거부되지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.balance_rules import InsufficientAvailableError
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    allows_negative,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua

_UNUSED_POOL = cast(asyncpg.Pool, None)  # 위 docstring 참조 — 실제로 참조되지 않는다.
_journal = PostgresJournalRepository(_UNUSED_POOL)
_balances = PostgresBalanceRepository(_UNUSED_POOL)
_audit = PostgresAuditEventRepository(_UNUSED_POOL)


class BridgeInsufficientBalanceError(Exception):
    """`USER:*:AVAILABLE` 브리지 포스팅이 잔액 부족(`allow_negative=False`)으로
    거부됐다 — `wallet_service.debit`가 `InsufficientBalanceError`로 변환한다."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _reconcile_ledger_with_projection(
    conn: asyncpg.Connection, user_id: UUID, currency: Currency
) -> None:
    """`user_wallets.balance`가 이 브리지 밖에서(관리자 도구, 테스트 픽스처의
    직접 SQL 등) 바뀔 수 있는 한, 매 브리지 호출 전에 원장을 그 값에 맞춰
    재동기화한다 — drift를 `PLATFORM:CASH_CLEARING` ↔ 사용자 계정
    `MANUAL_ADJUSTMENT`로 흡수한다(raw UPDATE로 조용히 맞추면 CASH_CLEARING과의
    복식부기 짝이 깨진다, §4.4 Σ=0 불변). 정상 경로(LC-11 백필 이후 오직
    이 브리지만 두 값을 함께 바꾸는 경우)에서는 drift가 항상 0이라 이
    분기가 실행되지 않는다. drift가 음수(투영이 원장보다 작음, 예: 위
    사유로 강제로 낮춰짐)여도 이 계정의 `held`는 브리지가 절대 건드리지
    않아 항상 0이므로 하향 조정은 `available < 0`을 절대 만들지 않는다."""
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
    await post_entry(
        conn, sync_event, journal=_journal, balances=_balances, audit=_audit, clock=_utcnow
    )


async def _post_and_project(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    related_purchase_id: int | None,
    *,
    debit_account: str,
    credit_account: str,
    signed_amount: Decimal,
) -> Decimal:
    currency = Currency.KRW
    await _reconcile_ledger_with_projection(conn, user_id, currency)
    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"bridge:{tx_type}:{uuid4()}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=currency,
        parties={},
        extra={"debit_account": debit_account, "credit_account": credit_account},
    )
    try:
        await post_entry(
            conn, event, journal=_journal, balances=_balances, audit=_audit, clock=_utcnow
        )
    except InsufficientAvailableError as exc:
        raise BridgeInsufficientBalanceError(str(exc)) from exc

    row = await conn.fetchrow(
        "UPDATE user_wallets SET balance = balance + $2, updated_at = now() "
        "WHERE user_id = $1 RETURNING balance",
        user_id, signed_amount,
    )
    if row is None:
        row = await conn.fetchrow(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) RETURNING balance",
            user_id, signed_amount,
        )
    await conn.execute(
        "INSERT INTO wallet_transactions "
        "(user_id, tx_type, amount, balance_after, related_purchase_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        user_id, tx_type, signed_amount, row["balance"], related_purchase_id,
    )
    return cast(Decimal, row["balance"])


async def bridge_debit(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    *,
    related_purchase_id: int | None = None,
) -> Decimal:
    return await _post_and_project(
        conn, user_id, amount, tx_type, related_purchase_id,
        debit_account=ua(user_id, UserSub.AVAILABLE),
        credit_account=PLATFORM_CASH_CLEARING,
        signed_amount=-amount,
    )


async def bridge_credit(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    *,
    related_purchase_id: int | None = None,
) -> Decimal:
    return await _post_and_project(
        conn, user_id, amount, tx_type, related_purchase_id,
        debit_account=PLATFORM_CASH_CLEARING,
        credit_account=ua(user_id, UserSub.AVAILABLE),
        signed_amount=amount,
    )
