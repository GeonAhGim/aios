"""LA-4 — market_data 캔들 중복 판정(dedupe) 순수 규칙.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §9.2 LA-4.

같은 (venue, instrument, timeframe, open_time)의 캔들이 둘 이상이면: 내용이
전부 같으면 1건만 남기고 `DUPLICATE_IDENTICAL`(info) 이슈를 낸다. 내용이
하나라도 다르면 어느 쪽이 옳은지 판정할 수 없으므로 그룹 전체를
`conflicts`로 격리하고 `DUPLICATE_CONFLICT`(reject) 이슈를 낸다.
`QualityIssue`는 LA-1 계약을 그대로 쓴다. `DedupeResult`는 이 리프가
생산하는 순수 출력이며 계약에 없다(LB-2 `CostBasisResult`와 동일 원칙).
I/O 없음 — 순수 함수.
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
    """`dedupe` 한 번의 결과. `kept`는 저장 대상(중복 제거 후), `conflicts`는
    양쪽 다 격리해야 하는 캔들, `issues`는 그룹별 판정 근거."""

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
