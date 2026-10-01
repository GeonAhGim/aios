"""DC-23 exact round trips, fail-closed partitions and million-tick streaming."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from time import perf_counter

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
        values.update(
            price=Decimal("123.45000000000000000001"), size=Decimal("1E-20"), aggressor="BUY"
        )
    else:
        values.update(
            bid_price=Decimal("123.4500"),
            ask_price=Decimal("124.0000"),
            bid_size=Decimal("0E-10"),
            ask_size=Decimal("1E-20"),
        )
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


@pytest.mark.parametrize(
    "change", [dict(ts_event=TS - 2), dict(instrument_id=ID[:-1] + "W"), dict(venue=Venue.BITGET)]
)
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


@pytest.mark.perf
@pytest.mark.timeout(600)
def test_million_ticks_bounded_arrow_memory(tmp_path: Path):
    store = TickParquetStorage(tmp_path, batch_size=65536)
    tick = record()
    baseline = pa.total_allocated_bytes()
    started = perf_counter()
    store.write_day(VENUE, ID, DAY, (tick for _ in range(1_000_000)), kind="trades")
    write_seconds = perf_counter() - started
    assert write_seconds < 200, write_seconds
    started = perf_counter()
    total = 0
    for batch in store.read_columns(VENUE, ID, DAY, kind="trades"):
        assert batch.num_rows <= 65536
        assert batch.nbytes < 32 * 1024 * 1024
        assert pa.total_allocated_bytes() - baseline < 64 * 1024 * 1024
        for name, value in tick.model_dump(mode="json").items():
            assert batch.column(name).unique().to_pylist() == [str(value)]
        total += batch.num_rows
    assert total == 1_000_000
    read_seconds = perf_counter() - started
    assert read_seconds < 20, read_seconds
    print(f"million ticks: write={write_seconds:.3f}s read={read_seconds:.3f}s")
    projected = next(store.read_columns(VENUE, ID, DAY, kind="trades", columns=["ts_event"]))
    assert projected.schema.names == ["ts_event"]


@pytest.mark.parametrize("kind", ["trades", "quotes"])
@pytest.mark.parametrize("conflict", [False, True])
def test_concurrent_publication_and_independent_replay(tmp_path, monkeypatch, kind, conflict):
    from src.foundation.market_data.adapters.storage import tick_parquet

    barrier = Barrier(2)
    link = tick_parquet.os.link

    def synchronized_link(source, destination):
        barrier.wait(timeout=10)
        link(source, destination)

    monkeypatch.setattr(tick_parquet.os, "link", synchronized_link)

    def publish(seq):
        try:
            return TickParquetStorage(tmp_path).write_day(
                VENUE, ID, DAY, [record(kind, seq=seq)], kind=kind
            )
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, [0, int(conflict)]))
    assert sum(result is not None for result in results) == (1 if conflict else 2)
    monkeypatch.setattr(tick_parquet.os, "link", link)
    reader = TickParquetStorage(tmp_path)
    rows = [
        row for batch in reader.read_columns(VENUE, ID, DAY, kind=kind) for row in batch.to_pylist()
    ]
    winner = int(rows[0]["seq"])
    path = next(result for result in results if result is not None)
    before = path.read_bytes()
    assert publish(winner) == path
    assert publish(winner + 10) is None
    assert path.read_bytes() == before
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("kind", ["trades", "quotes"])
def test_interrupted_stream_and_unvalidated_model_fail_closed(tmp_path, kind):
    store = TickParquetStorage(tmp_path, batch_size=1)

    def interrupted():
        yield record(kind)
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        store.write_day(VENUE, ID, DAY, interrupted(), kind=kind)
    invalid = record(kind).model_copy(update={"seq": -1})
    with pytest.raises(ValueError):
        store.write_day(VENUE, ID, DAY, [invalid], kind=kind)
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.tmp"))
    store.write_day(VENUE, ID, DAY, [record(kind)], kind=kind)


# ── D3 증빙: 실패 주입 + 게이트 적색 재현 ──────────────────────────────


def test_fail_injected_io_error_cleans_up(tmp_path, monkeypatch):
    """DC-23 D3: pq.ParquetWriter IOError → 파티션 반쪽쓰기·.tmp 누수 없음.

    의존성(side_effect) 예외 주입으로 실제 결함을 재현:
    Writer.__exit__ 이후 os.link 전에 IOError가 발생하면 finally 블록이
    temporary 파일을 반드시 정리해야 한다. 정리되지 않으면 다음 재시도에
    FileExistsError를 유발한다.
    """
    import pyarrow.parquet as pq_mod

    original_parquet_writer = pq_mod.ParquetWriter

    class FailingParquetWriter:
        def __init__(self, *args, **kwargs):
            self._real = original_parquet_writer(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._real.__exit__(*exc)

        def write_batch(self, batch):
            raise OSError("simulated disk full")

        def write_table(self, table):
            raise OSError("simulated disk full")

    monkeypatch.setattr(pq_mod, "ParquetWriter", FailingParquetWriter)
    store = TickParquetStorage(tmp_path, batch_size=2)
    with pytest.raises(OSError, match="simulated disk full"):
        store.write_day(VENUE, ID, DAY, [record(kind="trades")], kind="trades")
    # finally: temporary 파일이 남지 않았는지 확인
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert tmp_files == [], f".tmp 파일 누수: {tmp_files}"
    # 파티션 파일도 생성되지 않았어야 함
    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert parquet_files == [], f"반쪽쓰기 parquet: {parquet_files}"


def test_gate_red_corrupt_parquet_raises(tmp_path):
    """DC-23 D3 gate_red: 손상된 parquet 파일이 read_columns에서 실제로 실패함을 증명.

    이 테스트는 게이트(검증)가 실제로 적색으로 떨어지는지를 확인한다.
    유효한 파일을 작성한 뒤 바이너리를 훼손하고, read_columns가
    Exception을 raising 하는지를 단언한다 — 게이트가 무음 통과하지 않음을 확인.
    """
    store = TickParquetStorage(tmp_path, batch_size=2)
    path = store.write_day(VENUE, ID, DAY, [record("trades")], kind="trades")
    # 원본 바이트 저장
    original_bytes = path.read_bytes()
    # 파일 바이너리 훼손: 중간 영역을 0x00으로 덮어쓰기 (파라켓 구조 파괴)
    corrupted = bytearray(original_bytes)
    mid = len(corrupted) // 2
    for i in range(mid, min(mid + 100, len(corrupted))):
        corrupted[i] = 0x00
    path.write_bytes(bytes(corrupted))
    # 게이트 적색 재현: read_columns가 실제로 실패해야 함
    with pytest.raises((ValueError, OSError, pa.lib.ArrowInvalid)):
        list(store.read_columns(VENUE, ID, DAY, kind="trades"))
