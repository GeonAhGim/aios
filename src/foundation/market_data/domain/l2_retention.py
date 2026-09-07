"""RD-19 — L2 호가창 보존 정책(순수).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D5 "전체 깊이는 단기 보존, 집계·상위 N호가는 장기 보존" + "비용은 저장·
운영이다 — 보존 정책을 처음부터 넣는다."

세 등급으로 나눈다: `age < full_depth_ttl`인 레코드는 전체 깊이 그대로
(`KEEP_FULL`), `full_depth_ttl <= age < aggregate_ttl`인 레코드는
상위 N호가로 축소(`DOWNSAMPLE`), `age >= aggregate_ttl`인 레코드는
삭제(`DELETE`). 디스크 상한을 넘으면 축소본부터(오래된 순) 추가로
삭제하되, `full_depth_ttl` 이내의 전체 깊이 레코드는 이 정책이 스스로
지우지 않는다 — "단기 보존은 보장한다"는 D5 약속을 상한 초과라는
이유로 조용히 깨지 않는다(그 경우 `cap_still_exceeded=True`로 표면화해
호출자가 알아채게 한다. fail-closed).

I/O 없음 — 실제 파일 삭제·다운샘플 변환은 `adapters/ingest/l2_depth_store.py`
소관이다.
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
    """저장소가 보고하는 레코드 1개의 메타(실 파일/행에 대한 순수 값 사본)."""

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
    """`records`(임의 순서)에 대해 결정론적 보존 계획을 반환한다.

    `now`는 호출자가 넘기는 결정론적 시계 입력(가상 시계 테스트 지원) —
    이 함수는 wall clock을 읽지 않는다.
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
        # 오래된 순으로 축소본(집계/다운샘플 예정)부터 추가 삭제 — 단기
        # 보존 구간(KEEP_FULL)은 절대 건드리지 않는다(D5 약속).
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
