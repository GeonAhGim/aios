"""RD-2 — `domain/known_at.assert_point_in_time` 경계값 + FA-9 위임 증명 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §4 RD-A1,
§9 RD-2 DoD (c)(d).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core import bitemporal as bitemporal_module
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain import known_at as known_at_module
from src.foundation.research_data.domain.known_at import (
    PointInTimeViolationError,
    assert_point_in_time,
)
from tests.conftest import PerfBudget


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
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    item = _item(known_at=as_of_time)
    assert_point_in_time(item, as_of_time)  # raise 없음


def test_known_at_one_microsecond_after_as_of_rejected() -> None:
    """RD-2 DoD (c) — `known_at`이 `as_of`보다 1마이크로초라도 뒤면 거부.
    `>=`/`>`를 손으로 뒤집으면(예: `known_at < as_of`로 잘못 판정) 이
    테스트가 실패한다."""
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    item = _item(known_at=as_of_time + timedelta(microseconds=1))
    with pytest.raises(PointInTimeViolationError) as exc_info:
        assert_point_in_time(item, as_of_time)
    assert exc_info.value.error_code.value == "RD_POINT_IN_TIME_VIOLATION"


def test_known_at_before_as_of_passes() -> None:
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
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
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

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
    item = _item(known_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        assert_point_in_time(item, datetime(2026, 9, 8, 12, 0))


# ---- 성능 단언(DEPTH 감사 task-2724 D1 판정 근거, 1702/1701/2058 DEEPEN 선례와 동일 패턴) ----


@pytest.mark.perf
def test_assert_point_in_time_hot_path_performance(
    perf_budget: PerfBudget,
) -> None:
    # assert_point_in_time은 모든 read path가 아이템을 반환하기 전에 호출하는
    # 순수 함수(BitemporalRecord 구성 + FA-9 as_of 위임)다. 10,000회 호출이
    # 1s 내로 끝나야 한다 -- I/O 없는 순수 계약의 실측 증명.
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    item = _item(known_at=as_of_time - timedelta(days=1))
    iterations = 10_000

    perf_budget.assert_within(
        lambda: [assert_point_in_time(item, as_of_time) for _ in range(iterations)],
        budget_ms=1000.0,
        n=1,
    )


# ---- gate-red 재현(D2): 10x 지연 monkeypatch → 적색 --------------------------


@pytest.mark.perf
def test_assert_point_in_time_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(1000ms/10000회 = 0.1ms/회)을 1000배 초과하는
    CPU 시간을 가장하면 같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 호출당 100ms를 반환하므로
    best_of(p95) > 0.1ms budget로 적색 확정.
    """
    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 100ms를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.1  # 100ms = 0.1s per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    as_of_time = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    item = _item(known_at=as_of_time - timedelta(days=1))

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: assert_point_in_time(item, as_of_time),
            budget_ms=0.1,
            n=5,
            batch=1,
        )
