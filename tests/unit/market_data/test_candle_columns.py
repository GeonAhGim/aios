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


def test_to_candle_records_reconstructs_values_and_shares_key(
    perf_budget: PerfBudget,
) -> None:
    key = _key()
    columns = _columns(3)

    sample = perf_budget.assert_within(
        lambda: to_candle_records(columns, key),
        budget_ms=10,
        label="to_candle_records(3 rows)",
    )
    records = sample.result

    assert len(records) == 3
    assert all(r.key is key for r in records)
    assert [r.open_time for r in records] == columns.ts
    assert [r.close_time for r in records] == [ts + timedelta(minutes=1) for ts in columns.ts]
    assert [r.open for r in records] == columns.open
    assert [r.high for r in records] == columns.high
    assert [r.low for r in records] == columns.low
    assert [r.close for r in records] == columns.close
    assert [r.volume for r in records] == columns.volume
    assert [r.quote_volume for r in records] == columns.quote_volume


def test_to_candle_records_empty_columns_returns_empty_list() -> None:
    assert to_candle_records(_columns(0), _key()) == []
    # 부정: ts는 빈 리스트, 나머지 필드는 길이 1 — 길이 불일치 거부
    mismatched = CandleColumns(
        ts=[],
        open=[Decimal(100)],
        high=[Decimal(110)],
        low=[Decimal(90)],
        close=[Decimal(105)],
        volume=[Decimal(10)],
        quote_volume=[None],
    )
    with pytest.raises(MismatchedColumnLengthError):
        to_candle_records(mismatched, _key())


def test_candle_columns_len_matches_ts_length() -> None:
    """CandleColumns.__len__은 ts 길이를 반환하고, 모든 필드 배열의 길이가 ts와 같다."""
    cols = _columns(5)
    n = len(cols)
    assert n == 5
    assert len(cols.ts) == n
    assert len(cols.open) == n
    assert len(cols.high) == n
    assert len(cols.low) == n
    assert len(cols.close) == n
    assert len(cols.volume) == n
    assert len(cols.quote_volume) == n


# ── negative tests ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("extra", [-1, 1], ids=["one_short", "one_long"])
@pytest.mark.parametrize(
    "field",
    ["ts", "open", "high", "low", "close", "volume", "quote_volume"],
)
def test_to_candle_records_rejects_mismatched_column_lengths(field: str, extra: int) -> None:
    """Negative test: if any single column differs in length from the others
    (shorter or longer), reject instead of silently pairing misaligned rows
    (fail-closed). The error reports the offending length."""
    columns = _columns(3)
    arrays = {
        "ts": list(columns.ts),
        "open": list(columns.open),
        "high": list(columns.high),
        "low": list(columns.low),
        "close": list(columns.close),
        "volume": list(columns.volume),
        "quote_volume": list(columns.quote_volume),
    }
    if extra < 0:
        arrays[field] = arrays[field][:extra]
    else:
        arrays[field] = [*arrays[field], arrays[field][-1]]
    broken = CandleColumns(**arrays)

    with pytest.raises(MismatchedColumnLengthError) as excinfo:
        to_candle_records(broken, _key())

    # The message lists every length, so the odd column's length appears
    # whether it is `ts` (reported as n) or another column.
    expected = 3 + extra
    assert str(expected) in str(excinfo.value)


def test_to_candle_records_passes_none_to_model_construct_unchanged() -> None:
    """negative test: model_construct는 검증.skip하므로 None이 Decimal
    필드에 혼입되어도 즉시 예외를 던지지 않는다. downstream consumer가
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


# N/A(설계상 검증 위임: ohlc_sanity.check_candle가 write 시점에 가격·수량 검증,
# model_construct는 perf 목적으로 검증을 의도적 생략). DoD 1번의
# None/음수/0 가격·수량 명시 거부는 candle_columns 범위 밖.


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

    cpu_ms는 best-of-N 최소값으로 노이즈를 걷어냈고, wall_ms는
    실제 소요 시간(다른 프로세스에 눌린 대기 포함)을 기록한다.
    wall_ms가 cpu_ms를 크게 상회하면 이 워커가 코어를 뺏겼음을 나타낸다.
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
    # wall-clock 단언: cpu_ms와 wall_ms 차이가 50ms 이하면
    # 이 워커가 다른 프로세스에 크게 눌리지 않았음을 확인한다.
    assert sample.wall_ms - sample.cpu_ms < 50, (
        f"wall_ms({sample.wall_ms:.1f}) - cpu_ms({sample.cpu_ms:.1f}) = "
        f"{sample.wall_ms - sample.cpu_ms:.1f}ms exceeds 50ms budget"
    )
