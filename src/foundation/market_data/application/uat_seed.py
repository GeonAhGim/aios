"""UAT market-data seed: register three BITGET spot instruments and ingest 1h candles.

Idempotent: instruments that already exist are skipped (and not re-registered), and the
candle ingest goes through `ingest_candles`, whose store dedupes on the series key.
Failures (network, quality gate, unknown symbol) never raise out of the per-instrument
loop — the reason is recorded in that instrument's result entry instead.

Limitation: `BitgetIngestSource` is capped at 200 candles per request (LA-15 §10 R8), so a
14-day 1h window yields only the most recent ~8 days from the public endpoint.

Run: `python -m src.foundation.market_data.application.uat_seed` (reads DATABASE_URL).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.ingest_candles import ingest_candles
from src.foundation.market_data.application.register_instrument import (
    register_instrument,
)
from src.foundation.market_data.contracts.v1 import (
    IngestCandlesCommand,
    RegisterInstrumentCommand,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.ports.ingest_source import IngestSource

__all__ = ["SEED_SYMBOLS", "main", "seed_uat_market_data"]

SEED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
_LOOKBACK = timedelta(days=14)
_LISTING_AGE = timedelta(days=30)


async def _ensure_instrument(
    pool: asyncpg.Pool,
    refs: PostgresReferenceRepository,
    audit: PostgresAuditEventRepository,
    symbol: str,
    now: datetime,
) -> bool:
    """Register the instrument if absent. Returns True when newly registered.

    Status stays PENDING: `apply_lifecycle_event` does not persist the status transition,
    and `ingest_candles` accepts PENDING (it only rejects SUSPENDED/DELISTED).
    """
    async with pool.acquire() as conn:
        existing = await refs.get_instrument(conn, Venue.BITGET, symbol, now)
    if existing is not None:
        return False
    listed_at = now - _LISTING_AGE
    actor = uuid4()
    await register_instrument(
        pool,
        RegisterInstrumentCommand(
            venue=Venue.BITGET,
            venue_symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.0001"),
            listed_at=listed_at,
            actor_subject_id=actor,
            trace_id=uuid4(),
        ),
        refs=refs,
        audit=audit,
    )
    return True


async def seed_uat_market_data(
    pool: asyncpg.Pool,
    *,
    now: datetime,
    fetch_source: IngestSource | None = None,
) -> dict[str, object]:
    """Seed instruments and 14 days of 1h candles; return per-symbol results (never raises
    for per-symbol failures)."""
    refs = PostgresReferenceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    store = PostgresCandleStore(pool)
    cal = PostgresCalendarRepository(pool)
    batches = PostgresBatchRepository(pool)

    adapter = None
    source = fetch_source
    if source is None:
        # Public endpoints only: no API key; demo_mode keeps the paper header set.
        from src.exchanges.bitget.adapter import BitgetAdapter
        from src.foundation.market_data.adapters.bitget_ingest_source import BitgetIngestSource

        adapter = BitgetAdapter("", "", "", demo_mode=True)
        source = BitgetIngestSource(adapter)

    results: dict[str, object] = {}
    try:
        for symbol in SEED_SYMBOLS:
            entry: dict[str, object] = {"registered": False, "ingested": False, "reason": None}
            results[symbol] = entry
            try:
                entry["registered"] = await _ensure_instrument(pool, refs, audit, symbol, now)
                batch = await ingest_candles(
                    IngestCandlesCommand(
                        tenant_id=None,
                        venue=Venue.BITGET,
                        canonical_symbol=to_canonical(Venue.BITGET, symbol),
                        timeframe=Timeframe.H1,
                        range_start=now - _LOOKBACK,
                        range_end=now,
                        trace_id=uuid4(),
                    ),
                    source=source,
                    store=store,
                    refs=refs,
                    cal=cal,
                    batches=batches,
                    audit=audit,
                    pool=pool,
                    clock=lambda: now,
                )
                entry["ingested"] = True
                entry["verdict"] = batch.verdict.verdict.value
                entry["accepted"] = batch.verdict.accepted
            except Exception as exc:  # noqa: BLE001 - per-symbol isolation, reason is reported
                entry["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        if adapter is not None:
            await adapter.aclose()
    return results


async def _run() -> int:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        results = await seed_uat_market_data(pool, now=datetime.now(UTC))
    finally:
        await pool.close()
    sys.stdout.write(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    failed = [s for s, r in results.items() if isinstance(r, dict) and not r["ingested"]]
    return 1 if failed else 0


def main() -> int:
    """CLI entry point (exit 1 when any symbol failed; the stack script only warns)."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
