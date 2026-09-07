"""FA-15 -- 1-day replay-vs-current-table verification, wired into local CI.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15.

Usage:
    python scripts/replay_verify.py                     # last 24h, DATABASE_URL
    python scripts/replay_verify.py --hours 24 --as-of 2026-09-07T00:00:00+00:00

Exits 0 if every order/ledger-account touched in the window replays to
exactly the current table row (byte-identical digest, `src.core.eventstore.
replay.digest_state`); exits 1 and prints every mismatch to stderr
otherwise. Fail-closed by construction -- DoD(2) is that this checker can
actually fail, not just report OK (proven in
tests/integration/eventstore/test_replay_verify.py by tampering with a
`ledger_balance` row directly and asserting this script's own exit code).

The window is fixed at 1 day by default (task-2060 decision): the ledger
side folds the *entire* journal from sequence 1 every run (a stream's
current balance can only be known by folding its whole history -- there is
no cheaper "since yesterday" fold), so if journal volume ever makes this
exceed the local-CI budget, move this step to a nightly job with a longer
`--hours` rather than widening the per-commit window (see local_ci.py
registration site for the current wiring).

Positions are intentionally out of scope here (documented gap, not an
oversight): `projections/positions.py` (`snapshot_builder.fold`) needs an
`asset_class` per position that no table stores --
`application/rebuild_snapshot.py`'s docstring carries the identical gap in
production. Wiring it needs an instrument registry this leaf does not have.

Orders' and ledger's projection logic is reused as-is from FA-14
(src/core/eventstore/projections/{orders,ledger}.py, task-2050 decision) --
not re-implemented here, so "byte-identical" actually proves the projection
and the write path agree.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg

from src.core.eventstore import replay
from src.core.eventstore.projections import ledger as ledger_projection
from src.core.eventstore.projections import orders as orders_projection
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository

_ORDER_FIELDS = (
    "status",
    "version",
    "filled_quantity",
    "average_fill_price",
    "fee_total",
    "fee_currency",
)


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


def window(as_of: datetime, hours: int) -> tuple[datetime, datetime]:
    """`[as_of - hours, as_of)` -- the streams whose replay gets checked
    this run (which streams, not how much of their history is folded --
    each stream's replay still starts at its own beginning)."""
    return as_of - timedelta(hours=hours), as_of


async def _touched_order_ids(
    conn: asyncpg.Connection, start: datetime, end: datetime
) -> list[UUID]:
    rows = await conn.fetch(
        "SELECT DISTINCT order_id FROM order_events WHERE occurred_at >= $1 AND occurred_at < $2",
        start,
        end,
    )
    return [row["order_id"] for row in rows]


async def _touched_ledger_accounts(
    conn: asyncpg.Connection, start: datetime, end: datetime
) -> list[str]:
    rows = await conn.fetch(
        "SELECT DISTINCT la.account_code FROM ledger_posting_line lpl "
        "JOIN ledger_journal_entry lje ON lje.entry_id = lpl.entry_id "
        "JOIN ledger_account la ON la.account_id = lpl.account_id "
        "WHERE lje.posted_at >= $1 AND lje.posted_at < $2",
        start,
        end,
    )
    return [row["account_code"] for row in rows]


async def _order_pair(
    conn: asyncpg.Connection, order_id: UUID
) -> tuple[dict[str, Any], dict[str, Any]]:
    events = await PostgresOrderEventRepository().timeline(conn, order_id)
    fills = await FillsRepository().list_for_order(conn, order_id)
    projected = orders_projection.project(order_id, events, fills)
    replayed: dict[str, Any] = {
        "status": projected.status.value,
        "version": projected.version,
        "filled_quantity": projected.filled_quantity,
        "average_fill_price": projected.average_fill_price,
        "fee_total": projected.fee_total,
        "fee_currency": projected.fee_currency,
    }
    row = await conn.fetchrow(
        "SELECT status, version, filled_quantity, average_fill_price, fee_total, fee_currency "
        "FROM orders WHERE order_id = $1",
        order_id,
    )
    actual: dict[str, Any] = {field: None if row is None else row[field] for field in _ORDER_FIELDS}
    return replayed, actual


async def _ledger_pairs(
    conn: asyncpg.Connection,
    journal: PostgresJournalRepository,
    account_codes: list[str],
) -> dict[replay.StreamKey, tuple[dict[str, Any], dict[str, Any]]]:
    if not account_codes:
        return {}
    entries = await journal.list_since(conn, 0)
    balances = ledger_projection.project(entries)
    rows = await conn.fetch(
        "SELECT la.account_code, lb.balance, lb.last_entry_seq FROM ledger_balance lb "
        "JOIN ledger_account la ON la.account_id = lb.account_id "
        "WHERE la.account_code = ANY($1::text[])",
        account_codes,
    )
    actual_by_code = {row["account_code"]: row for row in rows}

    pairs: dict[replay.StreamKey, tuple[dict[str, Any], dict[str, Any]]] = {}
    for code in account_codes:
        balance = balances.get(code)
        replayed: dict[str, Any] = {
            "balance": None if balance is None else balance.balance,
            "last_entry_seq": None if balance is None else balance.last_entry_seq,
        }
        actual_row = actual_by_code.get(code)
        actual: dict[str, Any] = {
            "balance": None if actual_row is None else actual_row["balance"],
            "last_entry_seq": None if actual_row is None else actual_row["last_entry_seq"],
        }
        pairs[("ledger", code)] = (replayed, actual)
    return pairs


async def verify(pool: asyncpg.Pool, *, as_of: datetime, hours: int) -> replay.ReplayReport:
    """Load every stream touched in `window(as_of, hours)`, replay it via
    FA-14's projections, and diff against its current table row."""
    start, end = window(as_of, hours)
    journal = PostgresJournalRepository(pool)
    async with pool.acquire() as conn:
        order_ids = await _touched_order_ids(conn, start, end)
        account_codes = await _touched_ledger_accounts(conn, start, end)

        streams: dict[replay.StreamKey, tuple[dict[str, Any], dict[str, Any]]] = {}
        for order_id in order_ids:
            streams[("orders", str(order_id))] = await _order_pair(conn, order_id)
        streams.update(await _ledger_pairs(conn, journal, account_codes))

    return replay.verify_replay(streams)


async def _run(*, hours: int, as_of: datetime) -> int:
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    try:
        report = await verify(pool, as_of=as_of, hours=hours)
    finally:
        await pool.close()

    start, end = window(as_of, hours)
    print(
        f"replay_verify: window=[{start.isoformat()}, {end.isoformat()}) "
        f"streams={report.streams_checked} combined_digest={report.combined_digest}"
    )
    for diff in report.mismatches:
        print(
            f"MISMATCH {diff.domain}:{diff.key} replayed={diff.replayed_digest} "
            f"actual={diff.actual_digest}",
            file=sys.stderr,
        )
    if not report.ok:
        print(f"replay_verify: FAIL ({len(report.mismatches)} mismatch)", file=sys.stderr)
        return 1
    print("replay_verify: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=int, default=24, help="Replay window size in hours (default: 24)"
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Window end, ISO 8601 (default: now, UTC). Fixing this makes reruns reproducible.",
    )
    args = parser.parse_args()
    as_of = (
        datetime.now(timezone.utc) if args.as_of is None else datetime.fromisoformat(args.as_of)
    )
    return asyncio.run(_run(hours=args.hours, as_of=as_of))


if __name__ == "__main__":
    raise SystemExit(main())
