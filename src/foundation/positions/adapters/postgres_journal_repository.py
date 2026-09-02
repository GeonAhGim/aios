"""LB-9 — `PositionJournalRepository`(ports/journal_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-9.

`append()`는 `pos_journal`이 `(position_key, sequence_no)` 단위로 유일해야
하므로(LC-8b `ledger_journal_entry`의 전역 단일 체인과 다름) §5 표 그대로
`pg_advisory_xact_lock(hashtext('pos_journal'), hashtext(position_key))`
2-인자 형태로 position_key 단위 직렬화한다.

`pos_journal.tenant_id`/`account_id`는 NOT NULL이지만 `ports/journal_repository.py`
의 `append()` 시그니처에는 없다(이번 리프는 포트를 바꾸지 않는다). 유일한
해석은 "저널에 처음 append하기 전에 `pos_snapshot` 행(§4.3 "스냅샷 =
fold(저널)")이 이미 존재한다"는 전제다 — 호출자(LB-11 `record_fill`, 아직
미착수)가 포지션을 처음 열 때 `tenant_id`/`account_id`/`instrument_id`로
빈 스냅샷(quantity=0, last_journal_seq=0)을 `SnapshotRepository.upsert`로
먼저 만들어 두고, 그 다음에만 `journal.append()`를 호출한다는 순서를
가정한다. 스냅샷이 없으면 `UnknownPositionError`(POS_ACCOUNT_UNKNOWN,
재시도 불가) — fail-closed.

`digest`(§5: `sha256(qty_delta, price, fee, occurred_at)`)는 재전송 판별
전용이고, `entry_hash`/`prev_hash`는 저널 행 자체의 변조 감지 체인이다 —
LC-8b `hash_chain.py`와 같은 관계(용도가 다른 두 해시)를 이 파일 안에서
재구현한다(LB-1~7에 이 목적의 domain 모듈이 없다 — §9 LB-9 표가 이 리프의
산출물로 adapters 3개만 나열함).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import JournalEntryType, PositionJournalEntryView

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`position_key`에 대응하는 `pos_snapshot` 행이 없다 — 저널 append 전에
    포지션을 열어야 한다(POS_ACCOUNT_UNKNOWN, 재시도 불가)."""

    def __init__(self, position_key: str) -> None:
        super().__init__(f"알 수 없는 position_key(스냅샷 없음): {position_key!r}")
        self.position_key = position_key


class IdempotencyDigestMismatchError(Exception):
    """같은 `idempotency_key`가 이전과 다른 내용으로 재전송됐다(재시도 불가,
    POS_IDEMPOTENCY_DIGEST_MISMATCH)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency_key={key!r}: 재전송 다이제스트가 기존과 다릅니다.")
        self.key = key


def _digest(
    qty_delta: Decimal, price: Money | None, fee: Money | None, occurred_at: AwareDatetime
) -> str:
    canonical = {
        "qty_delta": str(qty_delta),
        "price": None if price is None else [str(price.amount), price.currency.value],
        "fee": None if fee is None else [str(fee.amount), fee.currency.value],
        "occurred_at": occurred_at.isoformat(),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def _entry_hash(
    prev: str | None,
    position_key: str,
    seq: int,
    entry_type: JournalEntryType,
    digest: str,
    occurred_at: AwareDatetime,
) -> str:
    payload = "|".join(
        [prev or "", position_key, str(seq), entry_type.value, digest, occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_to_view(row: asyncpg.Record) -> PositionJournalEntryView:
    price = (
        None
        if row["price"] is None
        else Money(amount=row["price"], currency=Currency(row["price_ccy"]))
    )
    fee = (
        None if row["fee"] is None else Money(amount=row["fee"], currency=Currency(row["fee_ccy"]))
    )
    return PositionJournalEntryView(
        id=row["id"],
        position_key=row["position_key"],
        sequence_no=row["sequence_no"],
        entry_type=JournalEntryType(row["entry_type"]),
        qty_delta=row["qty_delta"],
        price=price,
        fee=fee,
        realized_pnl_base=row["realized_pnl_base"],
        fx_rate=row["fx_rate"],
        fx_source=row["fx_source"],
        source_event_type=row["source_event_type"],
        source_event_id=row["source_event_id"],
        idempotency_key=row["idempotency_key"],
        prev_hash=row["prev_hash"],
        entry_hash=row["entry_hash"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
    )


class PostgresJournalRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(
        self,
        conn: asyncpg.Connection,
        *,
        position_key: str,
        entry_type: JournalEntryType,
        qty_delta: Decimal,
        price: Money | None,
        fee: Money | None,
        realized_pnl_base: Decimal,
        fx_rate: Decimal | None,
        fx_source: str | None,
        source_event_type: str,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: AwareDatetime,
    ) -> PositionJournalEntryView:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
            _LOCK_NAMESPACE,
            position_key,
        )

        new_digest = _digest(qty_delta, price, fee, occurred_at)
        existing = await conn.fetchrow(
            "SELECT * FROM pos_journal WHERE idempotency_key = $1", idempotency_key
        )
        if existing is not None:
            if existing["digest"] != new_digest:
                raise IdempotencyDigestMismatchError(idempotency_key)
            return _row_to_view(existing)

        snapshot_row = await conn.fetchrow(
            "SELECT tenant_id, account_id FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
        if snapshot_row is None:
            raise UnknownPositionError(position_key)

        last_row = await conn.fetchrow(
            "SELECT sequence_no, entry_hash FROM pos_journal "
            "WHERE position_key = $1 ORDER BY sequence_no DESC LIMIT 1",
            position_key,
        )
        next_seq = 1 if last_row is None else last_row["sequence_no"] + 1
        prev_hash: str | None = None if last_row is None else last_row["entry_hash"]
        new_hash = _entry_hash(
            prev_hash, position_key, next_seq, entry_type, new_digest, occurred_at
        )

        row = await conn.fetchrow(
            "INSERT INTO pos_journal "
            "(tenant_id, account_id, position_key, sequence_no, entry_type, qty_delta, "
            " price, price_ccy, fee, fee_ccy, realized_pnl_base, fx_rate, fx_source, "
            " source_event_type, source_event_id, idempotency_key, digest, prev_hash, "
            " entry_hash, occurred_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) "
            "RETURNING *",
            snapshot_row["tenant_id"],
            snapshot_row["account_id"],
            position_key,
            next_seq,
            entry_type.value,
            qty_delta,
            None if price is None else price.amount,
            None if price is None else price.currency.value,
            None if fee is None else fee.amount,
            None if fee is None else fee.currency.value,
            realized_pnl_base,
            fx_rate,
            fx_source,
            source_event_type,
            source_event_id,
            idempotency_key,
            new_digest,
            prev_hash,
            new_hash,
            occurred_at,
        )
        return _row_to_view(row)

    async def list_for(
        self, conn: asyncpg.Connection, position_key: str, from_seq: int = 0
    ) -> list[PositionJournalEntryView]:
        rows = await conn.fetch(
            "SELECT * FROM pos_journal WHERE position_key = $1 AND sequence_no > $2 "
            "ORDER BY sequence_no ASC",
            position_key,
            from_seq,
        )
        return [_row_to_view(row) for row in rows]

    async def last(
        self, conn: asyncpg.Connection, position_key: str
    ) -> PositionJournalEntryView | None:
        row = await conn.fetchrow(
            "SELECT * FROM pos_journal WHERE position_key = $1 ORDER BY sequence_no DESC LIMIT 1",
            position_key,
        )
        return None if row is None else _row_to_view(row)
