"""DC-6 — Coverage declaration query and merge (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-6, §4.1(fail-closed, same semantics as `coverage_spans` overlap-prohibit
EXCLUDE constraint), §9.2 DC-6.

A coverage declaration is an (instrument×asset_class×TF×period×quality_grade) axis
(`CoverageSpan`, contracts/v2/coverage.py — task-1127 decision: DC-7
`gaps.plan_fetch` depends on this return type, so it lives in contracts, not domain).
This module queries those declarations by instrument×timeframe and merges
overlapping or adjacent (adjacent) spans into one within the same
(instrument_id, venue, asset_class, timeframe, quality_grade) axis. If the axes
differ (different venue or quality_grade), spans are not merged even if their
periods overlap — they are independent declarations from different sources
and quality levels.

If overlaps remain in the merge result, the DB EXCLUDE constraint (§4.1) will
reject the data, so overlaps must be eliminated — `merge_spans` proves that
invariant in code. Two spans whose boundaries exactly touch
(`a.end_at == b.start_at`) are contiguous and should be merged; two spans with
a gap between them are discontinuous and should remain separate.

No repository lookup or I/O — the caller (application/adapters) passes an
already-fetched list of `CoverageSpan` objects. Filling periods outside
coverage with 0/NaN is not this module's responsibility (§4.1) — DC-7
`gaps.py` takes over that fail-closed determination.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan
from src.foundation.market_data.contracts.v2.instruments import Instrument

__all__ = ["merge_spans", "coverage_for"]

_AxisKey = tuple[str, str, str, str, str]


def _axis_key(span: CoverageSpan) -> _AxisKey:
    return (
        span.instrument_id,
        span.venue.value,
        span.asset_class.value,
        span.timeframe.value,
        span.quality_grade.value,
    )


def merge_spans(spans: Sequence[CoverageSpan]) -> list[CoverageSpan]:
    """Deterministically merge spans that overlap or touch within the same axis.

    Sort each axis group by `(start_at, end_at)` ascending, then scan —
    always produces the same result regardless of input order (deterministic).
    Return order is fixed: axis key ascending, then `start_at` ascending within.
    """
    groups: dict[_AxisKey, list[CoverageSpan]] = defaultdict(list)
    for span in spans:
        groups[_axis_key(span)].append(span)

    merged: list[CoverageSpan] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda s: (s.start_at, s.end_at))
        current = ordered[0]
        for candidate in ordered[1:]:
            if candidate.start_at <= current.end_at:
                # Overlap (start < current end) or boundary touch (start == current end) — merge.
                if candidate.end_at > current.end_at:
                    current = current.model_copy(update={"end_at": candidate.end_at})
            else:
                # Discontinuous span with a gap — leave separate.
                merged.append(current)
                current = candidate
        merged.append(current)
    return merged


def coverage_for(
    spans: Sequence[CoverageSpan], instrument: Instrument, tf: Timeframe
) -> list[CoverageSpan]:
    """Query and merge coverage declarations matching `instrument`×`tf`.

    `spans` may be a raw list mixing arbitrary instruments and timeframes —
    first filter by `instrument.instrument_id` and `tf`, then merge via
    `merge_spans`. Returns an empty list if no matching declaration exists
    (no coverage — the caller's basis for `DATA_COVERAGE_MISSING`).
    """
    matching = [
        span
        for span in spans
        if span.instrument_id == instrument.instrument_id and span.timeframe == tf
    ]
    return merge_spans(matching)
