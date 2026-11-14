"""LA-23b — domain/candle_columns.py 순수 규칙 테스트.

Spec: docs/design/ADR-2026-09-04-A-market-data-replay-perf.md#1,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from tests.support.perf_budget import PerfBudget


def _key() -> SeriesKey:
    return SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)


def _columns(n: int) -> CandleColumns:
    base = datetime(2026, 1, 1, tzinfo=UTC)
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

# 삭제된 테스트 (리뷰 25095):
# - test_to_candle_records_rejects_negative_prices: 이름과 달리 수용을 단언.
#   음수 가격 거부는 ohlc_sanity.check_candle(쓰기 시점) 책임. candle_columns은
#   model_construct로 값 검증을 건너뛰도록 설계됨(candle_columns.py:71-74).
# - test_to_candle_records_rejects_zero_volume: 같은 이유로 삭제.
#   volume=0 수용을 "거부"라는 이름으로 두면 안 됨.
# - test_to_candle_records_passes_none_to_model_construct_unchanged:
#   fail-open 동작을 고정하는 테스트. None 전달 테스트는 ohlc_sanity 책임.


def test_to_candle_records_rejects_mismatched_column_lengths() -> None:
    """negative test: 배열 길이가 다르면 조용히 어긋난 행을 짝짓지 않고
    거부한다(fail-closed)."""
    columns = _columns(3)

    # 각 필드별로 1개 짧게 만든 배열을 테스트한다.
    @pytest.mark.parametrize(
        "field,shorten",
        [
            ("open", lambda c: c.open[:-1]),
            ("high", lambda c: c.high[:-1]),
            ("low", lambda c: c.low[:-1]),
            ("close", lambda c: c.close[:-1]),
            ("volume", lambda c: c.volume[:-1]),
            ("quote_volume", lambda c: c.quote_volume[:-1]),
        ],
    )
    def _short_field(columns: CandleColumns, field: str, shorten) -> CandleColumns:
        return CandleColumns(
            ts=columns.ts,
            open=shorten(columns) if field != "open" else columns.open,
            high=shorten(columns) if field != "high" else columns.high,
            low=shorten(columns) if field != "low" else columns.low,
            close=shorten(columns) if field != "close" else columns.close,
            volume=shorten(columns) if field != "volume" else columns.volume,
            quote_volume=shorten(columns) if field != "quote_volume" else columns.quote_volume,
        )

    for field, shorten in [
        ("open", lambda c: c.open[:-1]),
        ("high", lambda c: c.high[:-1]),
        ("low", lambda c: c.low[:-1]),
        ("close", lambda c: c.close[:-1]),
        ("volume", lambda c: c.volume[:-1]),
        ("quote_volume", lambda c: c.quote_volume[:-1]),
    ]:
        short_col = CandleColumns(
            ts=columns.ts,
            open=shorten(columns) if field == "open" else columns.open,
            high=shorten(columns) if field == "high" else columns.high,
            low=shorten(columns) if field == "low" else columns.low,
            close=shorten(columns) if field == "close" else columns.close,
            volume=shorten(columns) if field == "volume" else columns.volume,
            quote_volume=shorten(columns) if field == "quote_volume" else columns.quote_volume,
        )
        with pytest.raises(MismatchedColumnLengthError):
            to_candle_records(short_col, _key())


# ── failure injection tests ──────────────────────────────────────────────────


def test_to_candle_records_propagates_unknown_timeframe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: duration()이 UnknownTimeframeError를 던질 때
    to_candle_records가 이를 삼키지 않고 전파하는지 검증한다."""
    key = _key()
    columns = _columns(2)

    def fake_duration(timeframe: Timeframe) -> None:
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

    def fake_model_construct(**kwargs: object) -> CandleRecord:
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

    def fake_duration_returns_string(timeframe: Timeframe) -> str:
        return "not a timedelta"

    monkeypatch.setattr(
        "src.foundation.market_data.domain.candle_columns.duration",
        fake_duration_returns_string,
    )

    with pytest.raises(TypeError):
        to_candle_records(columns, key)


# ── numeric performance assertion ────────────────────────────────────────────


@pytest.mark.perf
def test_to_candle_records_performance_under_10k_rows(perf_budget: PerfBudget) -> None:
    """성능 단언: 10,000개 행을 to_candle_records로 변환하는 데
    200ms 미만이어야 한다(컬럼 기반 접근의 성능 목표, CI 병렬 실행 안정화).

    PerfBudget.assert_within 은 sample.cpu_ms 를 단언한다(wall-clock 아님).
    perf marker 는 pyproject.toml addopts 에서 제외되지 않으므로 기본 실행에 포함됨.
    """
    n = 10_000
    base = datetime(2026, 1, 1, tzinfo=UTC)
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
        budget_ms=200,
        label=f"to_candle_records({n} rows)",
    )

    assert len(sample.result) == n
