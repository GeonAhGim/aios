"""UAT market-data seed: registration, candle ingest, idempotency, failure reporting.

Needs a real DB (TEST_DATABASE_URL / DATABASE_URL), like the market_data integration tests.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.application.uat_seed import SEED_SYMBOLS, seed_uat_market_data
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue

pytestmark = pytest.mark.asyncio


def _dsn() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


class _FakeSource:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def fetch_candles(self, venue, raw_symbol, tf, start, end):
        self.calls += 1
        if self.fail:
            raise TimeoutError("network down")
        top = end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        out = []
        for i in range(6):
            t = top - timedelta(hours=5 - i)
            out.append(
                CandleRecord(
                    key=SeriesKey(
                        venue=Venue.BITGET,
                        instrument_id=__import__("uuid").uuid4(),
                        timeframe=Timeframe.H1,
                    ),
                    open_time=t,
                    close_time=t + timedelta(hours=1),
                    open=Decimal("100"),
                    high=Decimal("110"),
                    low=Decimal("90"),
                    close=Decimal("105"),
                    volume=Decimal("1"),
                )
            )
        return out


async def _instrument_count(pool: asyncpg.Pool) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM md_instrument WHERE venue = 'BITGET' AND venue_symbol = ANY($1)",
        list(SEED_SYMBOLS),
    )


async def _candle_count(pool: asyncpg.Pool) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM md_candle c JOIN md_instrument i USING (instrument_id) "
        "WHERE i.venue = 'BITGET' AND i.venue_symbol = ANY($1)",
        list(SEED_SYMBOLS),
    )


async def test_registers_three_instruments_and_ingests_candles(pool):
    now = datetime.now(UTC)
    res = await seed_uat_market_data(pool, now=now, fetch_source=_FakeSource())
    assert set(res) == set(SEED_SYMBOLS)
    for entry in res.values():
        assert entry["ingested"] is True and entry["reason"] is None
    assert await _instrument_count(pool) == 3
    assert await _candle_count(pool) >= 3


async def test_rerun_is_idempotent_and_skips_registered(pool):
    now = datetime.now(UTC)
    await seed_uat_market_data(pool, now=now, fetch_source=_FakeSource())
    inst_before, candles_before = await _instrument_count(pool), await _candle_count(pool)
    res = await seed_uat_market_data(pool, now=now, fetch_source=_FakeSource())
    assert all(e["registered"] is False for e in res.values())
    assert await _instrument_count(pool) == inst_before == 3
    assert await _candle_count(pool) == candles_before


async def test_network_failure_returns_reason_not_exception(pool):
    src = _FakeSource(fail=True)
    res = await seed_uat_market_data(pool, now=datetime.now(UTC), fetch_source=src)
    assert src.calls == 3
    for entry in res.values():
        assert entry["ingested"] is False
        assert "TimeoutError" in str(entry["reason"])
    assert await _instrument_count(pool) == 3


class _PartialFailSource(_FakeSource):
    """Fails only for one raw symbol; the other symbols must still be seeded."""

    def __init__(self, bad_symbol: str) -> None:
        super().__init__()
        self.bad_symbol = bad_symbol

    async def fetch_candles(self, venue, raw_symbol, tf, start, end):
        if raw_symbol == self.bad_symbol:
            self.calls += 1
            raise TimeoutError("network down")
        return await super().fetch_candles(venue, raw_symbol, tf, start, end)


async def test_partial_failure_isolates_the_failing_symbol(pool):
    bad = SEED_SYMBOLS[1]
    res = await seed_uat_market_data(
        pool, now=datetime.now(UTC), fetch_source=_PartialFailSource(bad)
    )
    assert res[bad]["ingested"] is False and "TimeoutError" in str(res[bad]["reason"])
    healthy = [s for s in SEED_SYMBOLS if s != bad]
    assert all(res[s]["ingested"] is True for s in healthy)
    assert await _instrument_count(pool) == 3


async def test_failed_run_then_rerun_recovers_without_reregistering(pool):
    now = datetime.now(UTC)
    await seed_uat_market_data(pool, now=now, fetch_source=_FakeSource(fail=True))
    res = await seed_uat_market_data(pool, now=now, fetch_source=_FakeSource())
    assert all(e["registered"] is False for e in res.values())
    assert all(e["ingested"] is True for e in res.values())
    assert await _instrument_count(pool) == 3
