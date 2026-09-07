"""RD-19 — `domain/l2_retention.py` 단위테스트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D5 "전체 깊이는 단기 보존, 상위 N호가·집계는 장기 보존" + 디스크 상한
초과 시 축소본부터 삭제하되 단기 전체 깊이 보존은 절대 건드리지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.market_data.domain.l2_retention import (
    DepthRecordMeta,
    RecordKind,
    RetentionAction,
    RetentionPolicy,
    plan_retention,
)

_NOW = datetime(2026, 1, 30, tzinfo=timezone.utc)
_POLICY = RetentionPolicy(
    full_depth_ttl=timedelta(days=2),
    aggregate_ttl=timedelta(days=30),
    top_n=10,
    disk_cap_bytes=10_000,
    aggregate_size_bytes_estimate=100,
)


def _rec(
    record_id: str, *, days_old: int, kind: RecordKind = RecordKind.FULL, size: int = 1000
) -> DepthRecordMeta:
    return DepthRecordMeta(
        record_id=record_id, kind=kind, as_of=_NOW - timedelta(days=days_old), size_bytes=size
    )


def test_recent_full_depth_is_kept_as_is():
    plan = plan_retention([_rec("a", days_old=1)], _NOW, _POLICY)
    assert plan.actions["a"] is RetentionAction.KEEP_FULL
    assert plan.projected_bytes == 1000


def test_aged_full_depth_is_downsampled_not_deleted():
    plan = plan_retention([_rec("a", days_old=10)], _NOW, _POLICY)
    assert plan.actions["a"] is RetentionAction.DOWNSAMPLE
    assert plan.projected_bytes == _POLICY.aggregate_size_bytes_estimate


def test_existing_aggregate_within_window_is_kept():
    record = _rec("a", days_old=10, kind=RecordKind.AGGREGATE, size=100)
    plan = plan_retention([record], _NOW, _POLICY)
    assert plan.actions["a"] is RetentionAction.KEEP_AGGREGATE


def test_record_beyond_aggregate_ttl_is_deleted():
    record = _rec("a", days_old=40, kind=RecordKind.AGGREGATE, size=100)
    plan = plan_retention([record], _NOW, _POLICY)
    assert plan.actions["a"] is RetentionAction.DELETE
    assert plan.projected_bytes == 0


def test_disk_cap_prunes_oldest_aggregates_first_never_full_depth():
    """상한 초과 시 축소본을 오래된 순으로 추가 삭제하되, 단기 전체 깊이
    보존 구간(KEEP_FULL)은 절대 삭제 대상이 되지 않는다(D5 약속)."""
    records = [
        _rec("recent_full", days_old=1, size=8_000),  # KEEP_FULL, 무조건 유지
        _rec("old_agg_1", days_old=10, kind=RecordKind.AGGREGATE, size=2_000),
        _rec("old_agg_2", days_old=20, kind=RecordKind.AGGREGATE, size=2_000),
    ]
    plan = plan_retention(records, _NOW, _POLICY)

    assert plan.actions["recent_full"] is RetentionAction.KEEP_FULL
    # 가장 오래된 축소본부터 삭제돼 상한(10_000) 이하로 맞춘다.
    assert plan.actions["old_agg_2"] is RetentionAction.DELETE
    assert plan.projected_bytes <= _POLICY.disk_cap_bytes
    assert plan.cap_still_exceeded is False


def test_cap_exceeded_even_after_pruning_aggregates_is_surfaced_not_hidden():
    """negative — 전체 깊이만으로도 상한을 넘으면 단기 보존을 깨는 대신
    `cap_still_exceeded=True`로 표면화한다(조용한 위반 금지)."""
    policy = RetentionPolicy(
        full_depth_ttl=timedelta(days=2),
        aggregate_ttl=timedelta(days=30),
        top_n=10,
        disk_cap_bytes=1_000,
        aggregate_size_bytes_estimate=100,
    )
    records = [_rec("a", days_old=1, size=5_000)]  # KEEP_FULL, 5000 > 1000 상한
    plan = plan_retention(records, _NOW, policy)

    assert plan.actions["a"] is RetentionAction.KEEP_FULL  # 여전히 삭제되지 않는다
    assert plan.cap_still_exceeded is True


def test_growing_daily_full_depth_beyond_full_ttl_stays_under_cap():
    """24시간 단위로 30일치 전체 깊이 레코드가 쌓여도, ttl 지난 것들은
    축소돼 총 디스크 사용량이 상한을 넘지 않는다."""
    records = [_rec(f"day-{d}", days_old=d, size=1_000) for d in range(30)]
    plan = plan_retention(records, _NOW, _POLICY)

    assert plan.projected_bytes <= _POLICY.disk_cap_bytes
    assert plan.cap_still_exceeded is False


def test_policy_rejects_non_positive_top_n_or_cap():
    with pytest.raises(ValueError):
        RetentionPolicy(
            full_depth_ttl=timedelta(days=1),
            aggregate_ttl=timedelta(days=2),
            top_n=0,
            disk_cap_bytes=1,
            aggregate_size_bytes_estimate=1,
        )
