"""LA-6 — log-return rolling median/MAD spike + adjacent candle high/low ratio cap.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §9.2 LA-6.

Pure function — no I/O or global clock. Assumes `candles` have already passed
OHLC sanity (LA-4), so prices are positive (if that assumption breaks,
`Decimal.ln()` raises `InvalidOperation` — sanity/dedupe is outside the scope
of this leaf). Thresholds (default `k_mad`, MAD floor, high/low ratio cap) are
marked "Draft" in the spec and have not been validated against real exchange data
(unverified).
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    QualityIssueType,
    Severity,
)

__all__ = ["detect_spikes"]

_MIN_WINDOW = 10
_MAD_FLOOR = Decimal("0.0001")
_HL_RATIO_CAP = Decimal("3")


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _log_returns(candles: list[CandleRecord]) -> list[Decimal]:
    return [(cur.close / prev.close).ln() for prev, cur in zip(candles, candles[1:], strict=False)]


def _spike_issue(candle: CandleRecord, reason: str) -> QualityIssue:
    return QualityIssue(
        type=QualityIssueType.SPIKE,
        severity=Severity.WARN,
        open_time=candle.open_time,
        detail={"reason": reason},
    )


def _hl_ratio_exceeded(prev: CandleRecord, cur: CandleRecord) -> bool:
    """Return True when the high/low ratio exceeds `_HL_RATIO_CAP` vs. the previous candle."""
    if prev.high > 0 and cur.high > prev.high * _HL_RATIO_CAP:
        return True
    return bool(cur.low > 0 and prev.low > cur.low * _HL_RATIO_CAP)


def detect_spikes(
    candles: list[CandleRecord], window: int = 60, k_mad: Decimal = Decimal("8")
) -> list[QualityIssue]:
    """`§4.1` SPIKE(WARN) detection — emits at most 1 issue per candle if either
    of two independent channels fires (dedup). Channel 1: log-return exceeds
    `k_mad * MAD` from the trailing (excluding current candle) median. Channel 2:
    high/low ratio exceeds `_HL_RATIO_CAP` vs the previous candle (catches cases
    where close is normal but high/low spike). Skips Channel 1 when the trailing
    sample is smaller than `_MIN_WINDOW` (false-positive prevention — do not decide
    when there is insufficient data to judge)."""
    issues: list[QualityIssue] = []
    if len(candles) < 2:
        return issues
    log_returns = _log_returns(candles)
    for idx in range(1, len(candles)):
        cur, prev = candles[idx], candles[idx - 1]
        r = log_returns[idx - 1]
        trailing = log_returns[max(0, idx - 1 - window) : idx - 1]
        if len(trailing) >= _MIN_WINDOW:
            median = _median(trailing)
            mad = max(_median([abs(x - median) for x in trailing]), _MAD_FLOOR)
            if abs(r - median) > k_mad * mad:
                issues.append(_spike_issue(cur, "log_return_mad"))
                continue
        if _hl_ratio_exceeded(prev, cur):
            issues.append(_spike_issue(cur, "hl_ratio"))
    return issues
