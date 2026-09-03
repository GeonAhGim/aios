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
from typing import Any

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


def _row_to_view(row: asyncpg.Record | dict[str, Any]) -> PositionJournalEntryView:
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
        # lock은 항상 단독 왕복으로 남긴다. 아래 통합 SELECT의 FROM/JOIN 절에
        # 얹어 왕복 하나를 더 줄이는 시도는 PG가 FROM절을 lock 함수보다 먼저
        # 평가해(잠금 선점 전에 last_entry를 읽어버림) 20-way 동시 append에서
        # (position_key, sequence_no) UNIQUE 위반을 실제로 재현시켰다(task-653
        # 실측) — 되돌리지 말 것.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
            _LOCK_NAMESPACE,
            position_key,
        )

        new_digest = _digest(qty_delta, price, fee, occurred_at)

        # 멱등 조회 + 스냅샷 소유자(tenant/account) 조회 + 직전 행(sequence_no·
        # entry_hash) 조회를 LEFT JOIN 하나로 묶어 3왕복을 1왕복으로 줄인다.
        # snapshot이 없어도(신규 position_key) last_entry는 항상 빈 결과라
        # 로직에 영향이 없다.
        combined = await conn.fetchrow(
            "SELECT "
            " snap.tenant_id AS snap_tenant_id, snap.account_id AS snap_account_id, "
            " last_entry.sequence_no AS last_sequence_no, "
            " last_entry.entry_hash AS last_entry_hash, "
            " existing.id AS existing_id, existing.position_key AS existing_position_key, "
            " existing.sequence_no AS existing_sequence_no, "
            " existing.entry_type AS existing_entry_type, "
            " existing.qty_delta AS existing_qty_delta, existing.price AS existing_price, "
            " existing.price_ccy AS existing_price_ccy, existing.fee AS existing_fee, "
            " existing.fee_ccy AS existing_fee_ccy, "
            " existing.realized_pnl_base AS existing_realized_pnl_base, "
            " existing.fx_rate AS existing_fx_rate, existing.fx_source AS existing_fx_source, "
            " existing.source_event_type AS existing_source_event_type, "
            " existing.source_event_id AS existing_source_event_id, "
            " existing.idempotency_key AS existing_idempotency_key, "
            " existing.digest AS existing_digest, existing.prev_hash AS existing_prev_hash, "
            " existing.entry_hash AS existing_entry_hash, "
            " existing.occurred_at AS existing_occurred_at, "
            " existing.recorded_at AS existing_recorded_at "
            "FROM (SELECT $2::varchar AS position_key) AS target "
            "LEFT JOIN pos_snapshot snap ON snap.position_key = target.position_key "
            "LEFT JOIN LATERAL ("
            "  SELECT sequence_no, entry_hash FROM pos_journal "
            "  WHERE position_key = target.position_key "
            "  ORDER BY sequence_no DESC LIMIT 1"
            ") last_entry ON true "
            "LEFT JOIN pos_journal existing ON existing.idempotency_key = $1",
            idempotency_key,
            position_key,
        )
        assert combined is not None

        if combined["existing_id"] is not None:
            if combined["existing_digest"] != new_digest:
                raise IdempotencyDigestMismatchError(idempotency_key)
            return _row_to_view(
                {
                    "id": combined["existing_id"],
                    "position_key": combined["existing_position_key"],
                    "sequence_no": combined["existing_sequence_no"],
                    "entry_type": combined["existing_entry_type"],
                    "qty_delta": combined["existing_qty_delta"],
                    "price": combined["existing_price"],
                    "price_ccy": combined["existing_price_ccy"],
                    "fee": combined["existing_fee"],
                    "fee_ccy": combined["existing_fee_ccy"],
                    "realized_pnl_base": combined["existing_realized_pnl_base"],
                    "fx_rate": combined["existing_fx_rate"],
                    "fx_source": combined["existing_fx_source"],
                    "source_event_type": combined["existing_source_event_type"],
                    "source_event_id": combined["existing_source_event_id"],
                    "idempotency_key": combined["existing_idempotency_key"],
                    "prev_hash": combined["existing_prev_hash"],
                    "entry_hash": combined["existing_entry_hash"],
                    "occurred_at": combined["existing_occurred_at"],
                    "recorded_at": combined["existing_recorded_at"],
                }
            )

        if combined["snap_tenant_id"] is None:
            raise UnknownPositionError(position_key)

        last_sequence_no = combined["last_sequence_no"]
        next_seq = 1 if last_sequence_no is None else last_sequence_no + 1
        prev_hash: str | None = combined["last_entry_hash"]
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
            combined["snap_tenant_id"],
            combined["snap_account_id"],
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
