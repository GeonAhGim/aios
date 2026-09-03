"""LA-23b — domain/candle_columns.py 순수 규칙 테스트.

Spec: docs/design/ADR-2026-09-04-A-market-data-replay-perf.md#1,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
    to_candle_records,
)


def _key() -> SeriesKey:
    return SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)


def _columns(n: int) -> CandleColumns:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return CandleColumns(
        ts=[base + timedelta(minutes=i) for i in range(n)],
        open=[Decimal(100 + i) for i in range(n)],
        high=[Decimal(110 + i) for i in range(n)],
        low=[Decimal(90 + i) for i in range(n)],
        close=[Decimal(105 + i) for i in range(n)],
        volume=[Decimal(10) for _ in range(n)],
        quote_volume=[None for _ in range(n)],
    )


def test_to_candle_records_reconstructs_values_and_shares_key() -> None:
    key = _key()
    columns = _columns(3)

    records = to_candle_records(columns, key)

    assert len(records) == 3
    assert all(r.key is key for r in records)
    assert [r.open_time for r in records] == columns.ts
    assert [r.close_time for r in records] == [ts + timedelta(minutes=1) for ts in columns.ts]
    assert [r.open for r in records] == columns.open
    assert [r.close for r in records] == columns.close


def test_to_candle_records_empty_columns_returns_empty_list() -> None:
    assert to_candle_records(_columns(0), _key()) == []


def test_to_candle_records_rejects_mismatched_column_lengths() -> None:
    """negative test: 배열 길이가 다르면 조용히 어긋난 행을 짝짓지 않고
    거부한다(fail-closed)."""
    columns = _columns(3)
    short_close = CandleColumns(
        ts=columns.ts,
        open=columns.open,
        high=columns.high,
        low=columns.low,
        close=columns.close[:-1],
        volume=columns.volume,
        quote_volume=columns.quote_volume,
    )

    with pytest.raises(MismatchedColumnLengthError):
        to_candle_records(short_close, _key())


def test_candle_columns_len_matches_ts_length() -> None:
    assert len(_columns(5)) == 5
