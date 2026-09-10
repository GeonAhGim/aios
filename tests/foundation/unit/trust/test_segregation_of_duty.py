"""PLT-43 segregation_of_duty.py 단위테스트 — DB 없이 순수 함수만 검증.

DB/네트워크 실패 주입: 이 모듈은 I/O가 전혀 없는 순수 함수이므로(모듈
docstring 참조) DB 커넥션 끊김·네트워크 타임아웃을 직접 주입할 대상이
없다. 대신 실제로 발생 가능한 "크래시"는 호출자가 넘기는 식별자 자체가
DB-backed lazy proxy 등이라 `==` 비교 도중 예외를 던지는 경우이므로,
`_ExplodingId`로 그 상황을 시뮬레이션해 예외가 삼켜지지 않고
fail-closed로 전파되는지 검증한다(아래 test_comparison_crash_propagates_
fail_closed).
"""

import time
from uuid import uuid4

import pytest

from src.foundation.trust.domain.rules.segregation_of_duty import (
    SegregationOfDutyViolation,
    assert_actor_not_counterparty,
)


def test_allows_distinct_actor_and_counterparty() -> None:
    actor = uuid4()
    counterparty = uuid4()
    assert_actor_not_counterparty(actor, counterparty, action="mandate.activate")


def test_allows_when_counterparty_not_yet_assigned() -> None:
    """DUAL 승인의 첫 서명 전처럼 아직 counterparty가 없으면 통과한다."""
    assert_actor_not_counterparty(uuid4(), None, action="approval_request.dual_second_sign")


def test_rejects_identical_actor_and_counterparty() -> None:
    same_id = uuid4()
    with pytest.raises(SegregationOfDutyViolation) as excinfo:
        assert_actor_not_counterparty(same_id, same_id, action="break_glass.approve_grant")
    assert excinfo.value.actor_id == same_id
    assert excinfo.value.action == "break_glass.approve_grant"
    assert str(same_id) in str(excinfo.value)


def test_rejects_identical_non_uuid_ids() -> None:
    """UUID뿐 아니라 값 동등성을 지원하는 어떤 식별자 타입에도 동작한다."""
    with pytest.raises(SegregationOfDutyViolation):
        assert_actor_not_counterparty("user-1", "user-1", action="mandate.propose_amendment")


def test_rejects_identical_int_ids() -> None:
    """정수 PK를 식별자로 쓰는 호출처(레거시 테이블 등)에도 동일하게 거부한다."""
    with pytest.raises(SegregationOfDutyViolation):
        assert_actor_not_counterparty(42, 42, action="approval_request.dual_second_sign")


class _ExplodingId:
    """DB-backed lazy proxy 등, 비교 도중 크래시하는 식별자를 시뮬레이션한다."""

    def __eq__(self, other: object) -> bool:
        raise RuntimeError("identity comparison crashed (simulated DB/network fault)")

    def __hash__(self) -> int:
        return id(self)


def test_comparison_crash_propagates_fail_closed() -> None:
    """식별자의 `==` 자체가 크래시하면(DB 커넥션 끊김 등을 흉내) 예외가 삼켜져
    통과(fail-open)되지 않고 그대로 전파되어야 한다 — 이 원시타입은 fail-closed가
    기본이어야 한다."""
    exploding = _ExplodingId()
    with pytest.raises(RuntimeError, match="simulated DB/network fault"):
        assert_actor_not_counterparty(exploding, exploding, action="break_glass.approve_grant")


def test_bulk_calls_complete_within_latency_budget() -> None:
    """수치 성능 단언: PLT-43은 매 DUAL 2차 서명·CM-5 활성화 호출마다 동기 경로에서
    실행되므로(순수 함수, I/O 없음), 만 번 호출이 평균 50us/call을 넘으면 이 primitive가
    실수로 I/O를 갖게 됐다는 회귀 신호로 본다."""
    actor, counterparty = uuid4(), uuid4()
    iterations = 10_000
    start = time.perf_counter()
    for _ in range(iterations):
        assert_actor_not_counterparty(actor, counterparty, action="perf.bulk")
    elapsed = time.perf_counter() - start
    per_call_us = (elapsed / iterations) * 1_000_000
    assert per_call_us < 50, f"per-call latency {per_call_us:.2f}us exceeds 50us budget"
