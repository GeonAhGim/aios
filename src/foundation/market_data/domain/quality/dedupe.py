"""LA-4 — market_data candle deduplication pure rule.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §9.2 LA-4.

When two or more candles share the same (venue, instrument, timeframe, open_time):
if all contents are identical, keep one and emit a
`DUPLICATE_IDENTICAL` (info) issue. If any content differs, the group cannot be
resolved, so isolate the entire group into `conflicts` and emit a
`DUPLICATE_CONFLICT` (reject) issue.
`QualityIssue` follows the LA-1 contract. `DedupeResult` is the pure output
produced by this leaf and is not part of the contract (same principle as
LB-2 `CostBasisResult`).
No I/O — pure function.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    QualityIssueType,
    Severity,
    Timeframe,
    Venue,
)

__all__ = ["DedupeResult", "dedupe"]

_GroupKey = tuple[Venue, UUID, Timeframe, datetime]


@dataclass(frozen=True, slots=True)
class DedupeResult:
    """Result of one `dedupe` call. `kept` contains candles to store (after dedup),
    `conflicts` holds candles isolated from both sides,
    `issues` provides per-group resolution rationale."""

    kept: tuple[CandleRecord, ...]
    conflicts: tuple[CandleRecord, ...]
    issues: tuple[QualityIssue, ...]


def _group_key(c: CandleRecord) -> _GroupKey:
    return (c.key.venue, c.key.instrument_id, c.key.timeframe, c.open_time)


def dedupe(candles: list[CandleRecord]) -> DedupeResult:
    groups: dict[_GroupKey, list[CandleRecord]] = defaultdict(list)
    order: list[_GroupKey] = []
    for c in candles:
        k = _group_key(c)
        if k not in groups:
            order.append(k)
        groups[k].append(c)

    kept: list[CandleRecord] = []
    conflicts: list[CandleRecord] = []
    issues: list[QualityIssue] = []
    for k in order:
        members = groups[k]
        open_time = k[3]
        if len(members) == 1:
            kept.append(members[0])
            continue
        if all(m == members[0] for m in members[1:]):
            kept.append(members[0])
            issues.append(
                QualityIssue(
                    type=QualityIssueType.DUPLICATE_IDENTICAL,
                    severity=Severity.INFO,
                    open_time=open_time,
                    detail={"count": str(len(members))},
                )
            )
        else:
            conflicts.extend(members)
            issues.append(
                QualityIssue(
                    type=QualityIssueType.DUPLICATE_CONFLICT,
                    severity=Severity.REJECT,
                    open_time=open_time,
                    detail={"count": str(len(members))},
                )
            )
    return DedupeResult(kept=tuple(kept), conflicts=tuple(conflicts), issues=tuple(issues))
