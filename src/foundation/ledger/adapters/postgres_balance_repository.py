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

FA-10(`docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10`) put
a no-UPDATE trigger on `ledger_balance`, so `apply()` no longer issues a
literal `UPDATE` -- it does `target` (pre-write state found by account
code, pinned via a MATERIALIZED CTE) -> `prior` (DELETE that row only if
its seq matches the expectation) -> INSERT a new balance row computed from
`target`, all three steps in one statement. If `target` is empty (unknown
account), the INSERT is a no-op too, so the existing `row is None` branch
below still tells `UnknownAccountError` and `ConcurrencyConflictError`
apart exactly as before.

The `SELECT ... FOR UPDATE OF lb` that `get_for_update` used to issue is
incompatible with DELETE+INSERT -- Postgres only follows a row lock across
an "update chain" (a new version of the *same* physical row); an unrelated
DELETE followed by a fresh INSERT is not part of that chain, so if the
locked row gets DELETE+INSERT'd by another transaction, a blocked reader
that wakes up concludes the row is simply gone and drops it from the
result (reproduced in practice:
`test_get_balance_no_false_positive_drift_under_concurrent_commits` failed
with `UnknownAccountError`). So instead of a physical row lock, this locks
the account code itself as a key via
`pg_advisory_xact_lock(hashtextextended(account_code, 0))` -- released
automatically at end of transaction, and unaffected by the physical row
changing under DELETE+INSERT since the same account code always hashes to
the same lock key. Locking in sorted (`account_code` ascending) order is
the same deadlock-avoidance discipline the old `ORDER BY la.account_code`
provided.
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
        for account_code in sorted(set(account_ids)):
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", account_code
            )
        rows = await conn.fetch(
            "SELECT la.account_code, la.currency, lb.balance, lb.held, "
            "lb.pending_payout, lb.last_entry_seq, lb.updated_at "
            "FROM ledger_balance lb "
            "JOIN ledger_account la ON la.account_id = lb.account_id "
            "WHERE la.account_code = ANY($1::text[]) "
            "ORDER BY la.account_code",
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
            "WITH target AS MATERIALIZED ("
            " SELECT lb.account_id, lb.balance, lb.held, lb.pending_payout,"
            " lb.allow_negative, lb.last_entry_seq, la.account_code, la.currency"
            " FROM ledger_balance lb JOIN ledger_account la ON la.account_id = lb.account_id"
            " WHERE la.account_code = $1"
            "), prior AS ("
            " DELETE FROM ledger_balance"
            " WHERE account_id = (SELECT account_id FROM target)"
            " AND last_entry_seq = $4"
            " RETURNING account_id"
            ") "
            "INSERT INTO ledger_balance ("
            " account_id, balance, held, pending_payout, allow_negative,"
            " last_entry_seq, updated_at"
            ") "
            "SELECT t.account_id, t.balance + $2, t.held + $3, t.pending_payout,"
            " t.allow_negative, t.last_entry_seq + 1, now() "
            "FROM target t "
            "WHERE EXISTS (SELECT 1 FROM prior) "
            "RETURNING account_id, balance, held, pending_payout, last_entry_seq, updated_at, "
            "(SELECT account_code FROM target) AS account_code, "
            "(SELECT currency FROM target) AS currency",
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
