"""DC-23 exact round trips, fail-closed partitions and million-tick streaming."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.foundation.market_data.adapters.storage.tick_parquet import TickParquetStorage
from src.foundation.market_data.adapters.storage.warm_parquet import AsOfNotSupportedError
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.microstructure import QuoteL1, TradeTick

VENUE = Venue.BINANCE
ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
DAY = date(2026, 1, 1)
TS = 1767225600000000001


def record(kind="trades", **changes):
    values = dict(instrument_id=ID, venue=VENUE, ts_event=TS, ts_recv=TS + 123, seq=0)
    if kind == "trades":
        values.update(price=Decimal("123.45000000000000000001"), size=Decimal("1E-20"),
                      aggressor="BUY")
    else:
        values.update(bid_price=Decimal("123.4500"), ask_price=Decimal("124.0000"),
                      bid_size=Decimal("0E-10"), ask_size=Decimal("1E-20"))
    values.update(changes)
    return (TradeTick if kind == "trades" else QuoteL1)(**values)


@pytest.mark.parametrize("kind", ["trades", "quotes"])
def test_exact_round_trip(tmp_path, kind):
    store = TickParquetStorage(tmp_path, batch_size=2)
    originals = [record(kind, seq=i, ts_event=TS + i) for i in range(5)]
    path = store.write_day(VENUE, ID, DAY, originals, kind=kind)
    assert path == tmp_path / VENUE.value / ID / "2026-01-01" / f"{kind}.parquet"
    batches = list(store.read_columns(VENUE, ID, DAY, kind=kind))
    assert [b.num_rows for b in batches] == [2, 2, 1]
    model = TradeTick if kind == "trades" else QuoteL1
    loaded = [model.model_validate(row) for b in batches for row in b.to_pylist()]
    assert [r.model_dump_json() for r in loaded] == [r.model_dump_json() for r in originals]
    assert all(pa.types.is_string(f.type) for f in batches[0].schema)
    assert store.write_day(VENUE, ID, DAY, originals, kind=kind) == path
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        store.write_day(VENUE, ID, DAY, [record(kind, seq=99)], kind=kind)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [dict(ts_event=TS - 2), dict(instrument_id=ID[:-1] + "W"),
                                   dict(venue=Venue.BITGET)])
def test_partition_mismatch_never_publishes(tmp_path, change):
    store = TickParquetStorage(tmp_path, batch_size=1)
    with pytest.raises(ValueError, match="partition"):
        store.write_day(VENUE, ID, DAY, [record(), record(**change)], kind="trades")
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_fail_closed_inputs(tmp_path):
    store = TickParquetStorage(tmp_path)
    with pytest.raises(ValueError):
        store.write_day(VENUE, ID, DAY, [record("quotes")], kind="trades")
    with pytest.raises(ValueError):
        store.read_columns(VENUE, "../escape", DAY, kind="trades")
    with pytest.raises(ValueError):
        store.read_columns(VENUE, ID, DAY, kind="trades", columns=["typo"])
    with pytest.raises(AsOfNotSupportedError):
        store.read_columns(VENUE, ID, DAY, kind="trades", as_of=datetime.now(timezone.utc))
    with pytest.raises(AsOfNotSupportedError):
        store.write_day(VENUE, ID, DAY, [], kind="trades", as_of=datetime.now(timezone.utc))
    with pytest.raises(FileNotFoundError):
        list(store.read_columns(VENUE, ID, DAY, kind="trades"))


def test_empty_corrupt_and_wrong_schema(tmp_path):
    store = TickParquetStorage(tmp_path)
    path = store.write_day(VENUE, ID, DAY, [], kind="trades")
    assert list(store.read_columns(VENUE, ID, DAY, kind="trades")) == []
    path.write_bytes(b"broken parquet")
    with pytest.raises(pa.ArrowInvalid):
        list(store.read_columns(VENUE, ID, DAY, kind="trades"))
    pq.write_table(pa.table({"seq": [1]}), path)
    with pytest.raises(ValueError, match="schema"):
        list(store.read_columns(VENUE, ID, DAY, kind="trades"))


@pytest.mark.timeout(600)
def test_million_ticks_bounded_arrow_memory(tmp_path: Path):
    store = TickParquetStorage(tmp_path, batch_size=65536)
    tick = record()
    baseline = pa.total_allocated_bytes()
    store.write_day(VENUE, ID, DAY, (tick for _ in range(1_000_000)), kind="trades")
    total = 0
    for batch in store.read_columns(VENUE, ID, DAY, kind="trades"):
        assert batch.num_rows <= 65536
        assert batch.nbytes < 32 * 1024 * 1024
        assert pa.total_allocated_bytes() - baseline < 64 * 1024 * 1024
        for name, value in tick.model_dump(mode="json").items():
            assert batch.column(name).unique().to_pylist() == [str(value)]
        total += batch.num_rows
    assert total == 1_000_000
    projected = next(store.read_columns(VENUE, ID, DAY, kind="trades", columns=["ts_event"]))
    assert projected.schema.names == ["ts_event"]

