"""LC-15a — `PayoutRepository`(ports/payout_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.4, §5, §9 LC-15.

`create_batch`의 포트 시그니처에는 `currency`가 없다 — `postgres_hold_repository.py`
와 같은 이유로, 판매자의 `USER:{seller}:PENDING_PAYOUT` 계정에서 그대로
끌어온다(그 계정은 `post_entry`가 `PAYOUT_RELEASE`를 포스팅할 때 이미
검증했으므로 이 시점엔 항상 존재한다). `ledger_payout_item.amount`도 포트에
없으므로, 각 `capture_entry_id`가 남긴 `ledger_posting_line`에서 그 판매자
`PENDING_PAYOUT` 계정으로의 CREDIT 금액(=그 캡처가 배치에 기여한 정산액)을
그대로 조회해 쓴다 — 새 값을 계산하지 않고 이미 포스팅된 분개에서 읽기만
한다(드리프트 방지).

`(seller_user_id, period_end)` UNIQUE는 §5 C "정산 배치: INSERT ... ON
CONFLICT DO NOTHING"을 그대로 구현한다: 충돌 시 새로 만들지 않고 기존
배치+기존 `ledger_payout_item` 목록을 조회해 그대로 반환한다(멱등). 이
경로에서는 `capture_entry_ids` 인자를 다시 insert하지 않는다 —
`ledger_payout_item.capture_entry_id UNIQUE`가 이중 지급을 막지만, 애초에
같은 배치를 두 번 만드는 시도 자체를 조용히 무해하게 만드는 쪽이 낫다
(application/payouts.py가 재실행 멱등을 기대, §9 LC-15 DoD).

`mark_paid`는 105번 표준 `conditional_update`로 `RELEASED → PAID` 조건부
전이만 한다(포트 docstring 계약, `LEDGER_HOLD_STATE_INVALID`류 매핑은
호출자 책임). `external_ref`는 `ledger_payout_batch`에 저장할 컬럼이
없다(LC-7 마이그레이션 스키마 참고) — 포트 시그니처에는 남아 있으나 이
어댑터는 그 값을 영속화하지 않는다(호출자의 `PAYOUT_PAID` 분개 사건이
이미 그 값을 검증했다, `posting_rules._payout_paid`).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.foundation.ledger.contracts.v1 import PayoutBatchView, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua

_RETURNING_COLUMNS = (
    "batch_id, seller_user_id, period_start, period_end, amount, "
    "state, release_entry_id, paid_entry_id"
)


class UnknownPayoutAccountError(Exception):
    """`seller_user_id`의 `PENDING_PAYOUT` 계정이 `ledger_account`에 없다 —
    정산 배치는 미지 계정으로 조용히 만들어지지 않는다(fail-closed)."""

    def __init__(self, account_code: str) -> None:
        super().__init__(f"알 수 없는 account_code: {account_code!r}")
        self.account_code = account_code


class UnknownCaptureEntryError(Exception):
    """`capture_entry_id`가 판매자 `PENDING_PAYOUT` 계정으로의 CREDIT 행을
    남기지 않았다 — 정산 대상이 아닌 분개를 배치에 섞지 않는다."""

    def __init__(self, capture_entry_id: UUID) -> None:
        super().__init__(f"정산 대상 CREDIT 행을 찾을 수 없는 capture_entry_id: {capture_entry_id}")
        self.capture_entry_id = capture_entry_id


def _row_to_view(row: asyncpg.Record, capture_entry_ids: list[UUID]) -> PayoutBatchView:
    return PayoutBatchView(
        batch_id=row["batch_id"],
        seller_user_id=row["seller_user_id"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        amount=row["amount"],
        state=row["state"],
        capture_entry_ids=capture_entry_ids,
        release_entry_id=row["release_entry_id"],
        paid_entry_id=row["paid_entry_id"],
    )


class PostgresPayoutRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_batch(
        self,
        conn: asyncpg.Connection,
        *,
        batch_id: UUID,
        seller_user_id: UUID,
        period_start: date,
        period_end: date,
        amount: Decimal,
        capture_entry_ids: Sequence[UUID],
        release_entry_id: UUID | None,
    ) -> PayoutBatchView:
        account_code = ua(seller_user_id, UserSub.PENDING_PAYOUT)
        currency = await conn.fetchval(
            "SELECT currency FROM ledger_account WHERE account_code = $1", account_code
        )
        if currency is None:
            raise UnknownPayoutAccountError(account_code)

        state = "RELEASED" if release_entry_id is not None else "SCHEDULED"
        row = await conn.fetchrow(
            "INSERT INTO ledger_payout_batch "
            "(batch_id, seller_user_id, period_start, period_end, amount, currency, "
            " state, release_entry_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            "ON CONFLICT (seller_user_id, period_end) DO NOTHING "
            f"RETURNING {_RETURNING_COLUMNS}",
            batch_id,
            seller_user_id,
            period_start,
            period_end,
            amount,
            currency,
            state,
            release_entry_id,
        )
        if row is None:
            return await self._fetch_existing(conn, seller_user_id, period_end)

        for capture_entry_id in capture_entry_ids:
            item_amount = await conn.fetchval(
                "SELECT pl.amount FROM ledger_posting_line pl "
                "JOIN ledger_account la ON la.account_id = pl.account_id "
                "WHERE pl.entry_id = $1 AND la.account_code = $2 AND pl.side = 'CREDIT'",
                capture_entry_id,
                account_code,
            )
            if item_amount is None:
                raise UnknownCaptureEntryError(capture_entry_id)
            await conn.execute(
                "INSERT INTO ledger_payout_item (batch_id, capture_entry_id, amount) "
                "VALUES ($1, $2, $3)",
                row["batch_id"],
                capture_entry_id,
                item_amount,
            )
        return _row_to_view(row, list(capture_entry_ids))

    async def list_due(self, conn: asyncpg.Connection) -> list[PayoutBatchView]:
        rows = await conn.fetch(
            f"SELECT {_RETURNING_COLUMNS} FROM ledger_payout_batch "
            "WHERE state = 'RELEASED' ORDER BY period_end, seller_user_id"
        )
        return [await self._with_items(conn, row) for row in rows]

    async def mark_paid(
        self,
        conn: asyncpg.Connection,
        batch_id: UUID,
        *,
        paid_entry_id: UUID,
        external_ref: str,
    ) -> PayoutBatchView:
        row = await conditional_update(
            conn,
            table="ledger_payout_batch",
            id_column="batch_id",
            id_value=batch_id,
            expected_state_column="state",
            expected_state_value="RELEASED",
            set_values={"state": "PAID", "paid_entry_id": paid_entry_id},
            returning=_RETURNING_COLUMNS,
        )
        return await self._with_items(conn, row)

    async def _fetch_existing(
        self, conn: asyncpg.Connection, seller_user_id: UUID, period_end: date
    ) -> PayoutBatchView:
        row = await conn.fetchrow(
            f"SELECT {_RETURNING_COLUMNS} FROM ledger_payout_batch "
            "WHERE seller_user_id = $1 AND period_end = $2",
            seller_user_id,
            period_end,
        )
        assert row is not None  # ON CONFLICT DO NOTHING이 발동했다면 반드시 존재
        return await self._with_items(conn, row)

    async def _with_items(self, conn: asyncpg.Connection, row: asyncpg.Record) -> PayoutBatchView:
        item_rows = await conn.fetch(
            "SELECT capture_entry_id FROM ledger_payout_item WHERE batch_id = $1 ORDER BY item_id",
            row["batch_id"],
        )
        return _row_to_view(row, [item_row["capture_entry_id"] for item_row in item_rows])
