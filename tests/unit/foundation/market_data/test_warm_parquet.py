"""DC-14 — `adapters/storage/warm_parquet.py` 단위테스트(로컬 파일, 실 DB 불필요).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.2 DC-14.
DoD (a)~(e)를 각각 하나의 테스트로 반증한다.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from src.foundation.market_data.adapters.storage.warm_parquet import (
    AsOfNotSupportedError,
    WarmParquetStorage,
    YearMismatchError,
)
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

_KEY = SeriesKey(venue=Venue.BINANCE, instrument_id=uuid4(), timeframe=Timeframe.D1)


def _daily_columns(year: int, days: list[int]) -> CandleColumns:
    ts = [datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=d) for d in days]
    n = len(ts)
    return CandleColumns(
        ts=ts,
        open=[Decimal("100.1234567890") + Decimal(i) for i in range(n)],
        high=[Decimal("101.5000000001") + Decimal(i) for i in range(n)],
        low=[Decimal("99.0000000009") + Decimal(i) for i in range(n)],
        close=[Decimal("100.9999999999") + Decimal(i) for i in range(n)],
        volume=[Decimal("12.3400000000") + Decimal(i) for i in range(n)],
        quote_volume=[None if i % 2 else Decimal("1234.5600000000") for i in range(n)],
    )


def _digest(columns: CandleColumns) -> str:
    """운영 코드가 아니라 이 테스트에서만 쓰는 정준(canonical) 직렬화 —
    tzinfo 클래스 정체성(`timezone.utc` vs 동치인 `timezone(timedelta(0))`)에
    관계없이 값만 비교하려고 `isoformat()`/`str()`로 정규화한다."""
    parts: list[str] = []
    for i in range(len(columns)):
        parts.append(columns.ts[i].isoformat())
        parts.append(str(columns.open[i]))
        parts.append(str(columns.high[i]))
        parts.append(str(columns.low[i]))
        parts.append(str(columns.close[i]))
        parts.append(str(columns.volume[i]))
        parts.append("" if columns.quote_volume[i] is None else str(columns.quote_volume[i]))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def test_round_trip_is_byte_identical(tmp_path: Path):
    """DoD(a): 저장→로드한 컬럼 배열의 sha256 digest가 원본과 바이트 동일해야
    한다(근사·tolerance 비교 금지)."""
    store = WarmParquetStorage(tmp_path)
    original = _daily_columns(2026, [0, 1, 2, 3, 4])
    store.write_year(_KEY, 2026, original)

    loaded, missing = store.read_columns(
        _KEY,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 6, tzinfo=timezone.utc),
    )

    assert missing == ()
    assert _digest(loaded) == _digest(original)
    assert loaded.ts == original.ts
    assert loaded.open == original.open
    assert loaded.quote_volume == original.quote_volume


def test_partial_range_reports_explicit_missing_months(tmp_path: Path):
    """DoD(b): 요청 2026-01~2026-03 중 02월 캔들만 저장된 경우, 반환된
    컬럼은 02월분만 담고, 결측 구간은 01·03월로 명시돼야 한다(빈 결과로
    뭉개지 않는다)."""
    store = WarmParquetStorage(tmp_path)
    february_only = _daily_columns(2026, [31, 32, 33])  # Feb 1~3
    store.write_year(_KEY, 2026, february_only)

    loaded, missing = store.read_columns(
        _KEY,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 1, tzinfo=timezone.utc),
    )

    assert len(loaded) == 3
    assert all(ts.month == 2 for ts in loaded.ts)
    assert missing == (
        (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)),
        (datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 4, 1, tzinfo=timezone.utc)),
    )


def test_missing_year_file_reports_whole_range_missing(tmp_path: Path):
    store = WarmParquetStorage(tmp_path)

    loaded, missing = store.read_columns(
        _KEY,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 3, 1, tzinfo=timezone.utc),
    )

    assert len(loaded) == 0
    assert missing == (
        (datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2020, 3, 1, tzinfo=timezone.utc)),
    )


def test_reuses_candle_store_port_read_candles_columnar(tmp_path: Path):
    """DoD(c): DC-13 hot_postgres가 위임하는 `CandleStore` 포트와 같은
    이름·시그니처(`read_candles_columnar`)로 `CandleColumns`를 반환한다 —
    새 포트·새 DTO가 아니라 기존 반환형을 그대로 쓴다."""
    store = WarmParquetStorage(tmp_path)
    store.write_year(_KEY, 2026, _daily_columns(2026, [0, 1]))

    async def _call() -> CandleColumns:
        return await store.read_candles_columnar(
            None,
            _KEY,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            None,
        )

    columns = asyncio.run(_call())
    assert isinstance(columns, CandleColumns)
    assert len(columns) == 2


def test_read_candles_columnar_rejects_as_of(tmp_path: Path):
    store = WarmParquetStorage(tmp_path)
    store.write_year(_KEY, 2026, _daily_columns(2026, [0]))

    async def _call() -> None:
        await store.read_candles_columnar(
            None,
            _KEY,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    with pytest.raises(AsOfNotSupportedError):
        asyncio.run(_call())


def test_truncated_parquet_file_raises_instead_of_returning_empty(tmp_path: Path):
    """DoD(d): 파일 꼬리를 잘라낸 손상 입력은 빈 시리즈를 조용히 반환하지
    않고 예외로 표면화해야 한다."""
    store = WarmParquetStorage(tmp_path)
    path = store.write_year(_KEY, 2026, _daily_columns(2026, [0, 1, 2]))
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(Exception):  # noqa: B017 — pyarrow의 정확한 예외 타입은 버전 의존
        store.read_columns(
            _KEY,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
        )


def test_write_year_rejects_mismatched_year(tmp_path: Path):
    store = WarmParquetStorage(tmp_path)
    wrong_year = _daily_columns(2025, [0])

    with pytest.raises(YearMismatchError):
        store.write_year(_KEY, 2026, wrong_year)


def test_write_year_rejects_mismatched_column_lengths(tmp_path: Path):
    store = WarmParquetStorage(tmp_path)
    columns = _daily_columns(2026, [0, 1])
    broken = CandleColumns(
        ts=columns.ts,
        open=columns.open[:1],
        high=columns.high,
        low=columns.low,
        close=columns.close,
        volume=columns.volume,
        quote_volume=columns.quote_volume,
    )

    with pytest.raises(MismatchedColumnLengthError):
        store.write_year(_KEY, 2026, broken)


def test_only_pyarrow_import_is_used_for_parquet_io():
    """DoD(e): ArcticDB(BSL 1.1)·Timescale 컬럼스토어를 반입하지 않는다 —
    이 모듈이 실제로 import하는 parquet 관련 서드파티는 pyarrow뿐임을
    소스 텍스트로 확인한다."""
    src = Path("src/foundation/market_data/adapters/storage/warm_parquet.py").read_text(
        encoding="utf-8"
    )
    import_lines = [
        line for line in src.splitlines() if line.startswith(("import ", "from "))
    ]
    assert any("pyarrow" in line for line in import_lines)
    assert not any("arcticdb" in line.lower() for line in import_lines)
    assert not any("timescale" in line.lower() for line in import_lines)
