"""FA-12 -- application/ibor_view.py: IBOR (investment book of record) view.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-12
(task-2059 decision, PM 2026-09-08).

IBOR is "what we currently believe is true" -- corrections apply
immediately (§1). This module needs zero new tables: it recomputes fund
balances by re-folding the append-only `ledger_journal_entry`/
`ledger_posting_line` tables (already WORM, LC-6) filtered by
`posted_at <= cutoff`. Because the source is append-only and a row's
`posted_at` never changes, the same `(fund_id, cutoff)` pair always folds
to the same result -- this is what lets ABOR (`abor_snapshot.py`) treat a
recompute at a fixed cutoff as an immutable snapshot.

The fold itself reuses `domain/trial_balance.apply_entry` (LC-5) instead of
re-summing debit/credit deltas here (task-2058 decision: do not
reimplement LC-3/LC-4/LC-5). `apply_entry` does not care how lines are
grouped into entries, so every posting line in scope is folded as one
flat sequence.

`correction_pending` deliberately does not know about `ledger_abor_snapshot`
(task-2059 decision item 5) -- it only answers "has this fund posted
anything after `closing_recorded_at`". The caller (`abor_snapshot.py`,
which *is* allowed to know about the new table) supplies that cutoff after
reading it back from a persisted close marker. This keeps IBOR computable
even before any ABOR close ever happens.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import PostingLine, Side
from src.foundation.ledger.domain import trial_balance


class NaiveCutoffError(ValueError):
    """`cutoff`/`closing_recorded_at`가 tz-naive datetime이다 -- 105번 표준과
    동일하게 fail-closed로 거부한다(silent UTC 가정 금지)."""


def _require_tz_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None:
        raise NaiveCutoffError(
            f"{name}: naive datetime은 허용하지 않는다 -- tz-aware UTC만 사용한다"
        )


class IborView(BaseModel):
    """`fund_id`의 `cutoff`(포함, `posted_at <= cutoff`) 시점 재계산 잔액.

    `balances`는 계정코드별 net(차변-대변) -- `trial_balance.apply_entry`와
    동일한 부호 규약(§4.4 자산/비용 차변 증가는 이 값이 그대로 "잔액"이지만,
    부채/수익 계정은 부호가 뒤집힌 net이라는 점은 호출자가 알아야 한다.
    이 뷰는 원장 부호 규약을 재해석하지 않고 LC-5 net을 그대로 노출한다).
    """

    fund_id: UUID
    cutoff: AwareDatetime
    balances: dict[str, Decimal]


_BALANCES_AS_OF_SQL = (
    "SELECT la.account_code, pl.side, pl.amount, pl.currency "
    "FROM ledger_posting_line pl "
    "JOIN ledger_journal_entry e ON e.entry_id = pl.entry_id "
    "JOIN ledger_account la ON la.account_id = pl.account_id "
    "WHERE pl.fund_id = $1 AND e.posted_at <= $2"
)

_CORRECTION_PENDING_SQL = (
    "SELECT EXISTS ("
    "  SELECT 1 FROM ledger_posting_line pl "
    "  JOIN ledger_journal_entry e ON e.entry_id = pl.entry_id "
    "  WHERE pl.fund_id = $1 AND e.posted_at > $2"
    ")"
)


async def compute_ibor_view(
    pool: asyncpg.Pool, *, fund_id: UUID, cutoff: datetime
) -> IborView:
    """`fund_id`의 postings를 `posted_at <= cutoff`로 필터링해 재계산한다.

    WORM 소스(추가만 가능, 기존 행의 `posted_at`는 절대 바뀌지 않는다)라
    같은 `(fund_id, cutoff)`는 언제 호출해도 byte-identical한 `balances`를
    낸다 -- `abor_snapshot.close_period`가 이 함수의 결과를 그대로 마감
    스냅샷 값으로 영속화할 수 있는 이유다.
    """
    _require_tz_aware(cutoff, name="cutoff")
    async with pool.acquire() as conn:
        rows = await conn.fetch(_BALANCES_AS_OF_SQL, fund_id, cutoff)

    lines = [
        PostingLine(
            line_no=i,
            account_code=row["account_code"],
            side=Side(row["side"]),
            amount=row["amount"],
            currency=Currency(row["currency"]),
        )
        for i, row in enumerate(rows, start=1)
    ]
    balances = trial_balance.apply_entry({}, lines)
    return IborView(fund_id=fund_id, cutoff=cutoff, balances=balances)


async def correction_pending(
    pool: asyncpg.Pool, *, fund_id: UUID, closing_recorded_at: datetime
) -> bool:
    """마감된 as_of_date에 대해, `closing_recorded_at` 이후 이 fund에
    기록된(`posted_at >`) posting이 하나라도 있으면 `True`.

    이 신호는 마감 스냅샷 값을 바꾸지 않는다(§9 FA-12 DoD, task-2059
    decision item 5) -- 정정은 표시만 한다. `closing_recorded_at`이 정확히
    그 마감이 쓴 cutoff와 같아야 의미가 있다 -- 호출자가 다른 값을 넘기면
    이 함수는 그 값을 그대로 신뢰한다(스키마를 모르므로 검증할 수 없다).
    """
    _require_tz_aware(closing_recorded_at, name="closing_recorded_at")
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(_CORRECTION_PENDING_SQL, fund_id, closing_recorded_at)
        )
