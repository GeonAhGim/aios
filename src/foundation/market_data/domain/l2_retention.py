"""RD-19 — L2 order book retention policy (pure).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D5 "full depth is kept short-term, aggregated top-N is kept long-term" +
"cost is storage and operations — build the retention policy in from the
start."

Splits into three tiers: records with `age < full_depth_ttl` keep full
depth as-is (`KEEP_FULL`); records with `full_depth_ttl <= age <
aggregate_ttl` are downsampled to the top-N levels (`DOWNSAMPLE`); records
with `age >= aggregate_ttl` are deleted (`DELETE`). If the disk cap is
exceeded, downsampled records are deleted further (oldest first), but this
policy never deletes full-depth records within `full_depth_ttl` on its
own — it will not silently break the D5 promise that "short-term retention
is guaranteed" just because the cap is exceeded (instead it surfaces
`cap_still_exceeded=True` so the caller notices; fail-closed).

No I/O — actual file deletion / downsample conversion is the
responsibility of `adapters/ingest/l2_depth_store.py`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

__all__ = [
    "RecordKind",
    "RetentionAction",
    "DepthRecordMeta",
    "RetentionPolicy",
    "RetentionPlan",
    "plan_retention",
]


class RecordKind(str, Enum):
    FULL = "FULL"
    AGGREGATE = "AGGREGATE"


class RetentionAction(str, Enum):
    KEEP_FULL = "KEEP_FULL"
    KEEP_AGGREGATE = "KEEP_AGGREGATE"
    DOWNSAMPLE = "DOWNSAMPLE"
    DELETE = "DELETE"


@dataclass(frozen=True)
class DepthRecordMeta:
    """Metadata for a single record as reported by the store.

    A pure value copy of the actual file/row.
    """

    record_id: str
    kind: RecordKind
    as_of: datetime
    size_bytes: int


@dataclass(frozen=True)
class RetentionPolicy:
    full_depth_ttl: timedelta
    aggregate_ttl: timedelta
    top_n: int
    disk_cap_bytes: int
    aggregate_size_bytes_estimate: int

    def __post_init__(self) -> None:
        if self.aggregate_ttl <= self.full_depth_ttl:
            raise ValueError("aggregate_ttl은 full_depth_ttl보다 길어야 한다")
        if self.top_n <= 0 or self.disk_cap_bytes <= 0:
            raise ValueError("top_n·disk_cap_bytes는 양수여야 한다")


@dataclass(frozen=True)
class RetentionPlan:
    actions: dict[str, RetentionAction]
    projected_bytes: int
    cap_still_exceeded: bool


def plan_retention(
    records: Sequence[DepthRecordMeta], now: datetime, policy: RetentionPolicy
) -> RetentionPlan:
    """Returns a deterministic retention plan for `records` (in any order).

    `now` is a deterministic clock input passed in by the caller (supports
    virtual-clock testing) — this function never reads the wall clock.
    """
    actions: dict[str, RetentionAction] = {}
    projected_sizes: dict[str, int] = {}

    for record in records:
        age = now - record.as_of
        if age < policy.full_depth_ttl:
            actions[record.record_id] = RetentionAction.KEEP_FULL
            projected_sizes[record.record_id] = record.size_bytes
        elif age < policy.aggregate_ttl:
            if record.kind is RecordKind.FULL:
                actions[record.record_id] = RetentionAction.DOWNSAMPLE
                projected_sizes[record.record_id] = policy.aggregate_size_bytes_estimate
            else:
                actions[record.record_id] = RetentionAction.KEEP_AGGREGATE
                projected_sizes[record.record_id] = record.size_bytes
        else:
            actions[record.record_id] = RetentionAction.DELETE
            projected_sizes[record.record_id] = 0

    total = sum(projected_sizes.values())
    if total > policy.disk_cap_bytes:
        # Delete downsample-bound records (aggregate/to-be-downsampled) first,
        # oldest first — never touch the short-term retention window
        # (KEEP_FULL); that is the D5 promise.
        _prunable_actions = (RetentionAction.KEEP_AGGREGATE, RetentionAction.DOWNSAMPLE)
        prunable = sorted(
            (r for r in records if actions[r.record_id] in _prunable_actions),
            key=lambda r: r.as_of,
        )
        for record in prunable:
            if total <= policy.disk_cap_bytes:
                break
            total -= projected_sizes[record.record_id]
            actions[record.record_id] = RetentionAction.DELETE
            projected_sizes[record.record_id] = 0

    return RetentionPlan(
        actions=actions, projected_bytes=total, cap_still_exceeded=total > policy.disk_cap_bytes
    )
