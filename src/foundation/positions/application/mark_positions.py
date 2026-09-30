"""LB-14 — Apply mark price and FX to open snapshots to refresh unrealized PnL
(application/mark_positions.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-14.

MARK is a derived-value update that does not leave journal rows (distinct from
§4.3 "snapshot = fold(journal)" — it does not touch `pos_journal`, only
conditionally upserts `pos_snapshot.mark_price/mark_at/unrealized_pnl_base`).
When no mark is available or it is stale (`marks.mark` is `None`), overwrite
`mark_price`/`mark_at`/`unrealized_pnl_base` all with `None` — skipping the
update while leaving previous values in place would cause the caller to mistake
an old mark for a fresh one, resulting in a quiet mispricing
(task-654 decision, "no fallback to fill with previous values").

`domain/pnl.unrealized` fails immediately with `CurrencyMismatchError` when
`mark.currency` differs from `snapshot.avg_cost.currency` (that situation is a
mark-source wiring bug that FX cannot patch — the premise is that
`MarkPriceSource` always returns marks denominated in the position's cost
currency, a trust boundary analogous to `record_fill` not validating trade
currency). The only case this function actually looks up FX is after that
premise already holds and `avg_cost.currency` (=`mark.currency`) differs from
`base_currency` — `pnl.unrealized` uses the FX rate only for that difference.
If FX is needed but unavailable (`fx.FxRateMissingError`/`FxRateStaleError`),
record the mark and timestamp as-is but leave `unrealized_pnl_base` as `None`
(§3.2 taxonomy "POS_FX_RATE_MISSING → unrealized None/skip for that account,
0 prohibited").

If `last_journal_seq` changes between the mark lookup and upsert due to a
concurrent fill, `SnapshotRepository.upsert` raises `ConcurrencyConflictError`
— per that port's contract ("do not swallow this exception"), propagate it
here without swallowing it (retry policy is the scheduler's responsibility,
LB-17, outside this leaf's scope).

`contract_multiplier` is not stored in the snapshot (same constraint as
`record_fill.py`, exists only in `RecordFillCommand` with nowhere to store it
in `pos_snapshot`) — Phase 1 handles spot only (R6, derivative short positions
are test-only), so this leaf uses the default value (1) of `pnl.unrealized`
as-is.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import asyncpg

from src.data.models.base import FXRate
from src.foundation.positions.contracts.v1 import PositionSnapshotView
from src.foundation.positions.domain import pnl
from src.foundation.positions.domain.fx import FxRateMissingError
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.mark_price_source import MarkPriceSource
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

__all__ = ["mark_positions"]


async def mark_positions(
    tenant_id: UUID,
    account_id: UUID,
    *,
    snapshots: SnapshotRepository,
    marks: MarkPriceSource,
    fx: FxRateSource,
    pool: asyncpg.Pool,
    clock: Clock,
) -> list[PositionSnapshotView]:
    now = clock()
    async with pool.acquire() as conn:
        open_positions = await snapshots.list_open(conn, tenant_id, account_id)

    updated: list[PositionSnapshotView] = []
    for snapshot in open_positions:
        mark = await marks.mark(snapshot.position_key, now)
        if mark is None:
            fields: dict[str, object] = {
                "mark_price": None,
                "mark_at": None,
                "unrealized_pnl_base": None,
            }
        else:
            rate: FXRate | None = None
            if snapshot.avg_cost.currency != snapshot.base_currency:
                rate = await fx.rate(snapshot.avg_cost.currency, snapshot.base_currency, now)
            try:
                unrealized = pnl.unrealized(snapshot, mark, rate, now=now).unrealized
            except FxRateMissingError:
                unrealized = None
            fields = {"mark_price": mark, "mark_at": now, "unrealized_pnl_base": unrealized}

        new_snapshot = snapshot.model_copy(update=fields)
        async with pool.acquire() as conn:
            persisted = await snapshots.upsert(
                conn, new_snapshot, expected_seq=snapshot.last_journal_seq
            )
        updated.append(persisted)

    return updated
