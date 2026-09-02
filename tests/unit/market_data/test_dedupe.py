"""LA-4 — market_data/domain/quality/dedupe.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §8.1, §9.2 LA-4.

핵심 케이스(§8.1): 동일 내용 중복 → 1건 유지(DUPLICATE_IDENTICAL, info),
상이 내용 중복 → CONFLICT(양쪽 격리, DUPLICATE_CONFLICT, reject).
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
from src.foundation.market_data.domain.quality.dedupe import dedupe

UTC = timezone.utc
_KEY = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)
_OPEN = datetime(2026, 9, 3, 10, 5, tzinfo=UTC)


def _candle(open_time: datetime = _OPEN, **overrides: object) -> CandleRecord:
    fields: dict[str, object] = dict(
        key=_KEY,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )
    fields.update(overrides)
    return CandleRecord(**fields)  # type: ignore[arg-type]


def test_no_duplicates_keeps_everything() -> None:
    a = _candle()
    b = _candle(open_time=_OPEN + timedelta(minutes=1))
    result = dedupe([a, b])
    assert result.kept == (a, b)
    assert result.conflicts == ()
    assert result.issues == ()


def test_identical_duplicate_keeps_one() -> None:
    a = _candle()
    b = _candle()
    result = dedupe([a, b])
    assert result.kept == (a,)
    assert result.conflicts == ()
    assert len(result.issues) == 1
    assert result.issues[0].type is QualityIssueType.DUPLICATE_IDENTICAL
    assert result.issues[0].severity is Severity.INFO


def test_conflicting_duplicate_isolates_both() -> None:
    a = _candle()
    b = _candle(close=Decimal("106"))
    result = dedupe([a, b])
    assert result.kept == ()
    assert result.conflicts == (a, b)
    assert len(result.issues) == 1
    assert result.issues[0].type is QualityIssueType.DUPLICATE_CONFLICT
    assert result.issues[0].severity is Severity.REJECT


def test_mixed_series_dedupes_only_matching_keys() -> None:
    unique = _candle(open_time=_OPEN + timedelta(minutes=5))
    dup_a = _candle()
    dup_b = _candle()
    result = dedupe([unique, dup_a, dup_b])
    assert result.kept == (unique, dup_a)
    assert result.conflicts == ()
    assert len(result.issues) == 1
