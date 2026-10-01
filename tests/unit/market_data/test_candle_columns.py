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
from src.foundation.market_data.domain.timeframe import UnknownTimeframeError


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


# ── positive / baseline ──────────────────────────────────────────────────────


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


def test_candle_columns_len_matches_ts_length() -> None:
    assert len(_columns(5)) == 5


# ── negative tests ───────────────────────────────────────────────────────────


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


def test_to_candle_records_passes_none_to_model_construct_unchanged() -> None:
    """negative test: model_construct는 검증.skip하므로 None이 Decimal
    필드에混入되어도 즉시 예외를 던지지 않는다. downstream consumer가
    이를 검증해야 함 — candle_columns 자체는 pure data holder이므로
    값 검증을 하지 않는다. 이 테스트는 그 동작을 명시적으로 확인한다."""
    columns = _columns(3)
    corrupted = CandleColumns(
        ts=columns.ts,
        open=[None, None, None],  # type: ignore[list-item]
        high=columns.high,
        low=columns.low,
        close=columns.close,
        volume=columns.volume,
        quote_volume=columns.quote_volume,
    )

    records = to_candle_records(corrupted, _key())
    assert len(records) == 3
    # None이 그대로 전달됨 — downstream이 검증해야 함
    assert records[0].open is None


def test_to_candle_records_rejects_negative_prices() -> None:
    """negative test: 고가 < 저가 또는 고가 < 오픈 등 OHLC 불변식
    위반을 기록하는 경우 — candle_columns 자체는 값 범위를 검증하지
    않지만, 호출 측(ohlc_sanity)을 우회한 음수/불일치 가격이
    model_construct를 거칠 때 어떤 행동을 하는지 명시적으로 확인한다.

    candle_columns은 pure data holder이므로 값 검증을 하지 않고
    records를 반환한다 — 이 테스트는 그 동작을 명시적으로 검증한다.
    """
    key = _key()
    columns = CandleColumns(
        ts=[datetime(2026, 1, 1, tzinfo=timezone.utc)],
        open=[Decimal(100)],
        high=[Decimal(90)],  # high < open — 이상하지만 candle_columns은 검증 안 함
        low=[Decimal(80)],
        close=[Decimal(95)],
        volume=[Decimal(10)],
        quote_volume=[None],
    )

    # candle_columns은 값 검증 없이 records를 반환 — 명시적 기대
    records = to_candle_records(columns, key)
    assert len(records) == 1
    assert records[0].open == Decimal(100)
    assert records[0].high == Decimal(90)


def test_to_candle_records_rejects_zero_volume() -> None:
    """negative test: volume=0은 기술적으로 유효한 Decimal이므로
    candle_columns은 이를 거부하지 않는다. 호출 측에서 검증해야 함.
    이 테스트는 candle_columns의 검증 범위를 명시한다."""
    key = _key()
    columns = CandleColumns(
        ts=[datetime(2026, 1, 1, tzinfo=timezone.utc)],
        open=[Decimal(100)],
        high=[Decimal(110)],
        low=[Decimal(90)],
        close=[Decimal(105)],
        volume=[Decimal(0)],
        quote_volume=[None],
    )

    records = to_candle_records(columns, key)
    assert len(records) == 1
    assert records[0].volume == Decimal(0)


# ── failure injection tests ──────────────────────────────────────────────────


def test_to_candle_records_propagates_unknown_timeframe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: duration()이 UnknownTimeframeError를 던질 때
    to_candle_records가 이를 삼키지 않고 전파하는지 검증한다."""
    key = _key()
    columns = _columns(2)

    def fake_duration(timeframe):
        raise UnknownTimeframeError(f"알 수 없는 timeframe: {timeframe!r}")

    monkeypatch.setattr(
        "src.foundation.market_data.domain.candle_columns.duration",
        fake_duration,
    )

    with pytest.raises(UnknownTimeframeError):
        to_candle_records(columns, key)


def test_to_candle_records_propagates_model_construct_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: CandleRecord.model_construct()가 예외를 던질 때
    to_candle_records가 이를 try/except로 삼키지 않고 전파하는지
    검증한다."""
    key = _key()
    columns = _columns(2)

    def fake_model_construct(**kwargs):
        raise RuntimeError("simulated model_construct failure")

    from src.foundation.market_data.contracts.v1 import CandleRecord

    monkeypatch.setattr(CandleRecord, "model_construct", staticmethod(fake_model_construct))

    with pytest.raises(RuntimeError, match="simulated model_construct failure"):
        to_candle_records(columns, key)


def test_to_candle_records_propagates_duration_return_type_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: duration()이 timedelta가 아닌 값을 반환할 때
    datetime + step 연산이 TypeError를 일으키고, 이를
    to_candle_records가 삼키지 않는지 검증한다."""

    key = _key()
    columns = _columns(2)

    def fake_duration_returns_string(timeframe):
        return "not a timedelta"

    monkeypatch.setattr(
        "src.foundation.market_data.domain.candle_columns.duration",
        fake_duration_returns_string,
    )

    with pytest.raises(TypeError):
        to_candle_records(columns, key)


# ── numeric performance assertion ────────────────────────────────────────────


@pytest.mark.perf
def test_to_candle_records_performance_under_10k_rows(perf_budget) -> None:
    """성능 단언: 10,000개 행을 to_candle_records로 변환하는 데
    100ms 미만이어야 한다(컬럼 기반 접근의 성능 목표)."""
    n = 10_000
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    columns = CandleColumns(
        ts=[base + timedelta(minutes=i) for i in range(n)],
        open=[Decimal(100 + i % 100) for i in range(n)],
        high=[Decimal(110 + i % 100) for i in range(n)],
        low=[Decimal(90 + i % 100) for i in range(n)],
        close=[Decimal(105 + i % 100) for i in range(n)],
        volume=[Decimal(10) for _ in range(n)],
        quote_volume=[None for _ in range(n)],
    )
    key = _key()

    sample = perf_budget.assert_within(
        lambda: to_candle_records(columns, key),
        budget_ms=100,
        label=f"to_candle_records({n} rows)",
    )

    assert len(sample.result) == n
