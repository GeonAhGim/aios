"""LA-4 — market_data/domain/quality/ohlc_sanity.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §8.1, §9.2 LA-4.

핵심 케이스(§8.1): 6개 위반(low<=min, high>=max, volume>=0,
close_time==open_time+duration, tz-aware UTC, 값 유한성) 각각 REJECT,
정상 캔들은 0이슈.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.quality.ohlc_sanity import check_candle

UTC = timezone.utc
_KEY = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)
_OPEN = datetime(2026, 9, 3, 10, 5, tzinfo=UTC)
_CLOSE = _OPEN + timedelta(minutes=1)


def _candle(**overrides: object) -> CandleRecord:
    fields: dict[str, object] = dict(
        key=_KEY,
        open_time=_OPEN,
        close_time=_CLOSE,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )
    fields.update(overrides)
    return CandleRecord.model_construct(**fields)  # type: ignore[arg-type]


def test_valid_candle_has_no_issues() -> None:
    assert check_candle(_candle()) == []


def test_low_above_min_open_close_rejected() -> None:
    issues = check_candle(_candle(low=Decimal("101")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert issues[0].severity is Severity.REJECT


def test_high_below_max_open_close_rejected() -> None:
    issues = check_candle(_candle(high=Decimal("104")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert issues[0].severity is Severity.REJECT


def test_negative_volume_rejected() -> None:
    issues = check_candle(_candle(volume=Decimal("-1")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NEGATIVE_VOLUME
    assert issues[0].severity is Severity.REJECT


def test_close_time_misaligned_rejected() -> None:
    issues = check_candle(_candle(close_time=_OPEN + timedelta(minutes=2)))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.TIME_MISALIGNED
    assert issues[0].severity is Severity.REJECT


def test_naive_open_time_rejected() -> None:
    issues = check_candle(_candle(open_time=datetime(2026, 9, 3, 10, 5)))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NAIVE_DATETIME
    assert issues[0].severity is Severity.REJECT
    assert issues[0].open_time is None


def test_naive_close_time_rejected() -> None:
    issues = check_candle(_candle(close_time=datetime(2026, 9, 3, 10, 6)))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NAIVE_DATETIME


def test_non_finite_value_rejected() -> None:
    issues = check_candle(_candle(volume=Decimal("NaN")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert issues[0].severity is Severity.REJECT
    assert "volume" in issues[0].detail


def test_infinite_price_rejected() -> None:
    issues = check_candle(_candle(high=Decimal("Infinity")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
