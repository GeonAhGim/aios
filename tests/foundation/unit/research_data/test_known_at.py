"""RD-2 — `domain/known_at.assert_point_in_time` 경계값 + FA-9 위임 증명 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §4 RD-A1,
§9 RD-2 DoD (c)(d).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core import bitemporal as bitemporal_module
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain import known_at as known_at_module
from src.foundation.research_data.domain.known_at import (
    PointInTimeViolationError,
    assert_point_in_time,
)


def _item(*, known_at: datetime) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=known_at,
        known_at=known_at,
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )


def test_known_at_equal_as_of_passes() -> None:
    """RD-2 DoD (c) — 경계 포함: `known_at == as_of`는 통과."""
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    item = _item(known_at=as_of_time)
    assert_point_in_time(item, as_of_time)  # raise 없음


def test_known_at_one_microsecond_after_as_of_rejected() -> None:
    """RD-2 DoD (c) — `known_at`이 `as_of`보다 1마이크로초라도 뒤면 거부.
    `>=`/`>`를 손으로 뒤집으면(예: `known_at < as_of`로 잘못 판정) 이
    테스트가 실패한다."""
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    item = _item(known_at=as_of_time + timedelta(microseconds=1))
    with pytest.raises(PointInTimeViolationError) as exc_info:
        assert_point_in_time(item, as_of_time)
    assert exc_info.value.error_code.value == "RD_POINT_IN_TIME_VIOLATION"


def test_known_at_before_as_of_passes() -> None:
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    item = _item(known_at=as_of_time - timedelta(days=1))
    assert_point_in_time(item, as_of_time)  # raise 없음


def test_delegates_to_fa9_bitemporal_as_of_not_reimplemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RD-2 DoD (d) — 재구현 금지 반증: FA-9 `core.bitemporal.as_of`를
    monkeypatch로 가짜 판정으로 바꾸면 `assert_point_in_time`의 결과가
    그대로 따라 바뀌어야 한다. 이 함수가 이 모듈 안에서 자체 시각 비교
    규칙을 다시 짰다면(위임하지 않았다면) 아래 두 patch 모두 결과에 아무
    영향을 주지 못해 이 테스트가 실패한다."""
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    # 원래는 통과해야 하는 좌표(known_at < as_of)인데, as_of가 항상 "커버 없음"을
    # 반환하도록 바꾸면 위임하는 구현은 거부로 뒤집힌다.
    past_item = _item(known_at=as_of_time - timedelta(days=1))

    def _always_empty(
        records: Sequence[bitemporal_module.BitemporalRecord[object]],
        *,
        valid_time: datetime,
        tx_time: datetime,
    ) -> list[bitemporal_module.BitemporalRecord[object]]:
        return []

    monkeypatch.setattr(known_at_module, "as_of", _always_empty)
    with pytest.raises(PointInTimeViolationError):
        assert_point_in_time(past_item, as_of_time)

    # 반대로: 원래는 거부해야 하는 좌표(known_at > as_of)인데, as_of가 항상
    # "커버함"을 반환하도록 바꾸면 위임하는 구현은 통과로 뒤집힌다.
    future_item = _item(known_at=as_of_time + timedelta(days=1))

    def _always_match(
        records: Sequence[bitemporal_module.BitemporalRecord[object]],
        *,
        valid_time: datetime,
        tx_time: datetime,
    ) -> list[bitemporal_module.BitemporalRecord[object]]:
        return list(records)

    monkeypatch.setattr(known_at_module, "as_of", _always_match)
    assert_point_in_time(future_item, as_of_time)  # raise 없음 — 가짜 판정을 그대로 따름


def test_naive_as_of_rejected_by_fa9_kernel() -> None:
    """`as_of_time`이 naive면 FA-9 `as_of`가 `ValueError`로 거부한다(위임 증거 —
    이 모듈이 자체 naive 검사를 두지 않아도 FA-9가 대신 막는다)."""
    item = _item(known_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        assert_point_in_time(item, datetime(2026, 9, 8, 12, 0))
