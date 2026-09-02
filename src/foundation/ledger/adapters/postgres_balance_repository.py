"""LC-8b — `BalanceRepository`(ports/balance_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §5, §9 LC-8.

`account_id`(포트 시그니처)는 항상 `account_code` 문자열이다 — DB PK인
`ledger_account.account_id` UUID는 이 파일 안에서만 조인으로 해석한다.

`apply()`의 `expected_seq`는 105번 표준 `conditional_update`와 같은 낙관적
동시성 가드다: 호출 직전 값(`get_for_update`가 돌려준 `last_entry_seq`)과
현재 DB 값이 다르면 `ConcurrencyConflictError`. 새 `last_entry_seq`는
포트 시그니처에 별도 인자가 없으므로 SQL에서 `last_entry_seq + 1`로
1씩 전진시킨다 — 이 컬럼은 "전역 분개 sequence_no의 사본"이 아니라
"이 계정 행이 몇 번 갱신됐는지"를 세는 낙관적 락 버전 카운터로 다룬다
(포트 docstring의 "새 분개의 sequence_no로 갱신"은 "새 분개가 이 행을
건드릴 때마다 전진한다"는 뜻으로 해석 — `get_for_update`로 이미 잠근
행이라 정상 경로에서는 절대 충돌하지 않는다는 포트 docstring과 일치).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import BalanceView


class UnknownAccountError(Exception):
    """`account_code`가 `ledger_account`에 없다 — 원장은 미지 계정을 조용히
    만들지 않는다(fail-closed, ports/balance_repository.py docstring)."""

    def __init__(self, missing_codes: Sequence[str]) -> None:
        super().__init__(f"알 수 없는 account_code: {list(missing_codes)}")
        self.missing_codes = list(missing_codes)


def _row_to_balance(row: asyncpg.Record) -> BalanceView:
    balance: Decimal = row["balance"]
    held: Decimal = row["held"]
    return BalanceView(
        account_code=row["account_code"],
        balance=balance,
        held=held,
        available=balance - held,
        pending_payout=row["pending_payout"],
        currency=Currency(row["currency"]),
        last_entry_seq=row["last_entry_seq"],
        as_of=row["updated_at"],
    )


class PostgresBalanceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_for_update(
        self, conn: asyncpg.Connection, account_ids: Sequence[str]
    ) -> dict[str, BalanceView]:
        if not account_ids:
            return {}
        rows = await conn.fetch(
            "SELECT la.account_code, la.currency, lb.balance, lb.held, "
            "lb.pending_payout, lb.last_entry_seq, lb.updated_at "
            "FROM ledger_balance lb "
            "JOIN ledger_account la ON la.account_id = lb.account_id "
            "WHERE la.account_code = ANY($1::text[]) "
            "ORDER BY la.account_code "
            "FOR UPDATE OF lb",
            list(account_ids),
        )
        found = {row["account_code"]: _row_to_balance(row) for row in rows}
        missing = set(account_ids) - found.keys()
        if missing:
            raise UnknownAccountError(sorted(missing))
        return found

    async def apply(
        self,
        conn: asyncpg.Connection,
        account_id: str,
        delta_balance: Decimal,
        delta_held: Decimal,
        expected_seq: int,
    ) -> BalanceView:
        row = await conn.fetchrow(
            "UPDATE ledger_balance AS lb SET "
            "balance = lb.balance + $2, "
            "held = lb.held + $3, "
            "last_entry_seq = lb.last_entry_seq + 1, "
            "updated_at = now() "
            "FROM ledger_account AS la "
            "WHERE la.account_id = lb.account_id "
            "AND la.account_code = $1 "
            "AND lb.last_entry_seq = $4 "
            "RETURNING lb.balance, lb.held, lb.pending_payout, lb.last_entry_seq, "
            "lb.updated_at, la.account_code, la.currency",
            account_id,
            delta_balance,
            delta_held,
            expected_seq,
        )
        if row is None:
            exists = await conn.fetchval(
                "SELECT 1 FROM ledger_account WHERE account_code = $1", account_id
            )
            if not exists:
                raise UnknownAccountError([account_id])
            raise ConcurrencyConflictError(
                f"ledger_balance.account_code={account_id}: last_entry_seq가 "
                f"기대값({expected_seq})과 다릅니다(동시 갱신 충돌) — "
                "get_for_update로 다시 조회 후 재시도하세요."
            )
        return _row_to_balance(row)
