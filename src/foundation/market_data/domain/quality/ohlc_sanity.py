"""LA-4 — market_data single-candle sanity (OHLC sanity) pure rules.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §9.2 LA-4.

Reuses `QualityIssue`/`QualityIssueType`/`Severity` from LA-1 contract
(contracts/v1) without overriding. Independently checks the six rules in
§2.2 (low<=min(open,close), high>=max(open,close), volume>=0,
close_time==open_time+duration, tz-aware UTC, value finiteness), emitting
a separate `QualityIssue` (severity=REJECT) for each violation. No I/O —
pure functions.

If a naive datetime is passed, comparing it with an aware one would raise,
so skip that check and emit NAIVE_DATETIME only. If values are non-finite
(NaN/Infinity), Decimal ordering (`>`, `<`) raises `InvalidOperation`,
so skip OHLC/volume comparisons as well.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    QualityIssueType,
    Severity,
)
from src.foundation.market_data.domain.timeframe import duration

__all__ = ["check_candle"]


def check_candle(c: CandleRecord) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    def add(t: QualityIssueType, ot: datetime | None, detail: dict[str, str]) -> None:
        issues.append(QualityIssue(type=t, severity=Severity.REJECT, open_time=ot, detail=detail))

    open_naive = c.open_time.tzinfo is None
    close_naive = c.close_time.tzinfo is None
    if open_naive or close_naive:
        add(
            QualityIssueType.NAIVE_DATETIME,
            None,
            {"open_naive": str(open_naive), "close_naive": str(close_naive)},
        )
    else:
        expected_close = c.open_time + duration(c.key.timeframe)
        if c.close_time != expected_close:
            add(
                QualityIssueType.TIME_MISALIGNED,
                c.open_time,
                {"expected_close": str(expected_close), "actual_close": str(c.close_time)},
            )
    ot: datetime | None = None if open_naive else c.open_time

    values: dict[str, Decimal] = {
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
    }
    if c.quote_volume is not None:
        values["quote_volume"] = c.quote_volume
    non_finite = {k: str(v) for k, v in values.items() if not v.is_finite()}
    if non_finite:
        add(QualityIssueType.OHLC_INCONSISTENT, ot, non_finite)
        return issues

    min_oc, max_oc = min(c.open, c.close), max(c.open, c.close)
    if c.low > min_oc:
        add(
            QualityIssueType.OHLC_INCONSISTENT,
            ot,
            {"rule": "low<=min(open,close)", "low": str(c.low), "min": str(min_oc)},
        )
    if c.high < max_oc:
        add(
            QualityIssueType.OHLC_INCONSISTENT,
            ot,
            {"rule": "high>=max(open,close)", "high": str(c.high), "max": str(max_oc)},
        )
    if c.volume < 0:
        add(QualityIssueType.NEGATIVE_VOLUME, ot, {"volume": str(c.volume)})
    return issues
