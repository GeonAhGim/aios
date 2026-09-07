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

FA-10(`docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10`)이
`ledger_balance`에 UPDATE 금지 트리거를 걸었으므로, `apply()`는 더 이상
literal `UPDATE`를 쓰지 않는다 — `target`(계정 코드로 찾은 치환 전 상태를
MATERIALIZED CTE로 고정) → `prior`(기대 seq와 일치할 때만 그 행을 DELETE)
→ `target`에서 읽은 값으로 새 잔액 행을 INSERT, 세 단계를 한 문장으로
묶는다. `target`이 비어 있으면(미지 계정) INSERT도 그냥 0행이라 기존과
동일하게 `row is None` 분기(아래)가 `UnknownAccountError`/
`ConcurrencyConflictError`를 가른다.

`get_for_update`가 쓰던 `SELECT ... FOR UPDATE OF lb`는 DELETE+INSERT와
호환되지 않는다 — Postgres는 행 잠금을 "업데이트 체인"(같은 물리 행의
새 버전)에 대해서만 따라가고, 무관한 DELETE 다음의 새 INSERT는 그 체인이
아니므로, 잠그고 있던 행이 다른 트랜잭션에서 DELETE+INSERT되면 blocked
리더가 깨어났을 때 "행이 사라졌다"고 판단해 결과에서 빠뜨린다(실측:
`test_get_balance_no_false_positive_drift_under_concurrent_commits`가
`UnknownAccountError`로 재현). 그래서 물리 행 잠금 대신
`pg_advisory_xact_lock(hashtextextended(account_code, 0))`으로 계정
코드 자체를 키로 잠근다 — 트랜잭션이 끝나면 자동 해제되고, 물리 행이
바뀌어도(DELETE+INSERT) 같은 계정 코드는 항상 같은 잠금 키로 직렬화된다.
정렬 순서(`account_code` 오름차순)로 잠그는 것은 기존 `ORDER BY
la.account_code`와 동일하게 교착 방지용이다.
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
