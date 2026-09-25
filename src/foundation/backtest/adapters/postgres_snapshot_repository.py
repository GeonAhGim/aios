"""L32 -- asyncpg implementation of the `market_bar_snapshot` table (§3.7 M4).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2.4
(`adapters/postgres_snapshot_repository.py` row: "`market_bar_snapshot`
테이블(§3.7 M4)" -> `PostgresSnapshotRepository(pool)`), §5
("`market_bar_snapshot` INSERT | PK 해시 `ON CONFLICT DO NOTHING`"), §9 L32
("저장->로드->해시 동일").

Backs the `save(ref, bars) -> None` / `load(snapshot_hash) -> tuple[
BarSnapshotRef, list[Candle]] | None` contract the spec's `ports/
snapshot_repository.py` row describes (that Protocol file is a separate
leaf, L26, not yet published -- this adapter exposes the same two methods
directly so `event_loop`/tests can depend on the concrete class without
waiting on the Protocol file).

`snapshot_hash` is the table's primary key and is produced only by
`backtest.domain.snapshot.compute_bar_snapshot_hash` (never recomputed or
reassembled here) -- `save` trusts the caller's `ref.snapshot_hash` and
relies on `ON CONFLICT (snapshot_hash) DO NOTHING` for the "same input
snapshot saved twice is a no-op" contract (R1 reproducibility: identical
bars + source + as_of always yield the identical hash, so a second save
under that same hash is always a duplicate of the first, never a
legitimate distinct version -- there is nothing to reconcile).
"""
from __future__ import annotations

import json

import asyncpg

from src.data.models.market_data import Candle
from src.foundation.backtest.domain.snapshot import BarSnapshotRef

__all__ = ["PostgresSnapshotRepository"]

_INSERT_SQL = (
    "INSERT INTO market_bar_snapshot "
    "(snapshot_hash, symbol, exchange, timeframe, from_time, to_time, "
    " bar_count, source, as_of, bars) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb) "
    "ON CONFLICT (snapshot_hash) DO NOTHING"
)

_SELECT_SQL = "SELECT * FROM market_bar_snapshot WHERE snapshot_hash = $1"


def _bar_to_json(bar: Candle) -> dict[str, object]:
    return bar.model_dump(mode="json")


def _bar_from_json(raw: dict[str, object]) -> Candle:
    return Candle.model_validate(raw)


class PostgresSnapshotRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, ref: BarSnapshotRef, bars: list[Candle]) -> None:
        if ref.bar_count != len(bars):
            raise ValueError(
                "PostgresSnapshotRepository.save: "
                f"ref.bar_count={ref.bar_count} != len(bars)={len(bars)}"
            )
        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_SQL,
                ref.snapshot_hash,
                ref.symbol,
                ref.exchange,
                ref.timeframe,
                ref.from_time,
                ref.to_time,
                ref.bar_count,
                ref.source,
                ref.as_of,
                json.dumps([_bar_to_json(bar) for bar in bars]),
            )

    async def load(
        self, snapshot_hash: str
    ) -> tuple[BarSnapshotRef, list[Candle]] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, snapshot_hash)
        if row is None:
            return None
        ref = BarSnapshotRef(
            snapshot_hash=row["snapshot_hash"],
            symbol=row["symbol"],
            exchange=row["exchange"],
            timeframe=row["timeframe"],
            from_time=row["from_time"],
            to_time=row["to_time"],
            bar_count=row["bar_count"],
            source=row["source"],
            as_of=row["as_of"],
        )
        bars = [_bar_from_json(raw) for raw in json.loads(row["bars"])]
        return ref, bars
