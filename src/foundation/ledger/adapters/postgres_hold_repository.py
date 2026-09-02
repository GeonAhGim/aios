"""LC-13 — `HoldRepository`(ports/hold_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.5, §9 LC-13.

`ledger_hold.account_id`는 DB FK(UUID)이고 포트가 주고받는 `account_code`는
문자열이다 — 이 파일 안에서만 `ledger_account`를 조인해 해석한다
(`postgres_balance_repository.py`와 동일 원칙, LC-8b). `currency`는 포트
시그니처에 없으므로 `INSERT ... SELECT ... FROM ledger_account`로 그 계정의
`currency`를 그대로 끌어온다(그 계정은 `post_entry`가 이미 검증했으므로
`create()` 시점엔 항상 존재한다).

`create()`는 `(purpose, reference)` UNIQUE 위반을 잡지 않고 그대로
전파한다(포트 docstring 계약 — 호출자 `application/purchase_flow.py`가
동시 홀드 경합 판정에 이 예외를 쓴다, task-424 decision: 재시도 루프 대신
UNIQUE 위반 → 도메인 에러 변환).

`transition()`은 105번 표준 `conditional_update`로 `expected_state`
조건부 UPDATE한다 — 실패하면 `ConcurrencyConflictError`(포트 docstring
계약, `LEDGER_HOLD_STATE_INVALID` 매핑은 호출자 책임).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.foundation.ledger.contracts.v1 import HoldState, HoldView

_RETURNING_COLUMNS = (
    "hold_id, account_id, amount, purpose, reference, state, expires_at, entry_id"
)


class UnknownHoldAccountError(Exception):
    """`account_code`가 `ledger_account`에 없다 — 홀드는 미지 계정을 조용히
    만들지 않는다(fail-closed, `ports/balance_repository.py`의
    `UnknownAccountError`와 동일 원칙)."""

    def __init__(self, account_code: str) -> None:
        super().__init__(f"알 수 없는 account_code: {account_code!r}")
        self.account_code = account_code


async def _row_to_hold(conn: asyncpg.Connection, row: asyncpg.Record) -> HoldView:
    account_code = await conn.fetchval(
        "SELECT account_code FROM ledger_account WHERE account_id = $1", row["account_id"]
    )
    return HoldView(
        hold_id=row["hold_id"],
        account_code=account_code,
        amount=row["amount"],
        purpose=row["purpose"],
        reference=row["reference"],
        state=HoldState(row["state"]),
        expires_at=row["expires_at"],
        entry_id=row["entry_id"],
    )


class PostgresHoldRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        conn: asyncpg.Connection,
        *,
        hold_id: UUID,
        account_code: str,
        amount: Decimal,
        purpose: str,
        reference: str,
        expires_at: datetime,
        entry_id: UUID,
    ) -> HoldView:
        row = await conn.fetchrow(
            f"INSERT INTO ledger_hold "
            f"(hold_id, account_id, amount, currency, purpose, reference, state, "
            f" expires_at, entry_id) "
            f"SELECT $1, la.account_id, $2, la.currency, $3, $4, 'PENDING', $5, $6 "
            f"FROM ledger_account la WHERE la.account_code = $7 "
            f"RETURNING {_RETURNING_COLUMNS}",
            hold_id,
            amount,
            purpose,
            reference,
            expires_at,
            entry_id,
            account_code,
        )
        if row is None:
            raise UnknownHoldAccountError(account_code)
        return HoldView(
            hold_id=row["hold_id"],
            account_code=account_code,
            amount=row["amount"],
            purpose=row["purpose"],
            reference=row["reference"],
            state=HoldState(row["state"]),
            expires_at=row["expires_at"],
            entry_id=row["entry_id"],
        )

    async def transition(
        self,
        conn: asyncpg.Connection,
        hold_id: UUID,
        *,
        expected_state: HoldState,
        new_state: HoldState,
        entry_id: UUID,
    ) -> HoldView:
        row = await conditional_update(
            conn,
            table="ledger_hold",
            id_column="hold_id",
            id_value=hold_id,
            expected_state_column="state",
            expected_state_value=expected_state.value,
            set_values={"state": new_state.value, "settled_entry_id": entry_id},
            returning=_RETURNING_COLUMNS,
        )
        return await _row_to_hold(conn, row)
