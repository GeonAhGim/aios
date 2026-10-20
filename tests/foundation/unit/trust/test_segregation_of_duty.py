"""PLT-43 segregation_of_duty.py 단위테스트 — DB 없이 순수 함수만 검증.

DB/네트워크 실패 주입: 이 모듈은 I/O가 전혀 없는 순수 함수이므로(모듈
docstring 참조) DB 커넥션 끊김·네트워크 타임아웃을 직접 주입할 대상이
없다. 대신 실제로 발생 가능한 "크래시"는 호출자가 넘기는 식별자 자체가
DB-backed lazy proxy 등이라 `==` 비교 도중 예외를 던지는 경우이므로,
`_ExplodingId`로 그 상황을 시뮬레이션해 예외가 삼켜지지 않고
fail-closed로 전파되는지 검증한다(아래 test_comparison_crash_propagates_
fail_closed).
"""

from uuid import uuid4

import pytest

from src.foundation.trust.domain.rules.segregation_of_duty import (
    SegregationOfDutyViolation,
    assert_actor_not_counterparty,
)
from tests.support.perf_budget import PerfBudget


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


@pytest.mark.perf
def test_cpu_measurement_converges_to_zero_for_fast_call(
    perf_budget: PerfBudget,
) -> None:
    """CPU 측정 0 수렴 검증: `assert_actor_not_counterparty`는 순수 비교만
    수행하므로 `perf_budget.sample`가 측정하는 CPU 시간이 1ms 미만으로
    수렴해야 한다. Windows의 `time.process_time()` 약 15.6ms 양자화 오차가
    batch로 줄어드는지 확인한다.

    불변식(I-03): 순수 도메인 로직은 1ms 미만이어야 한다.
    """
    actor, counterparty = uuid4(), uuid4()

    def _one_call() -> None:
        assert_actor_not_counterparty(actor, counterparty, action="perf.convergence")

    sample = perf_budget.sample(_one_call, batch=64)

    # CPU 시간이 1ms 미만 (양자화 오차 포함)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"순수 도메인 로직은 1ms 미만이어야 함 (budget<1ms)"
    )


@pytest.mark.perf
def test_gate_red_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(1ms)을 100배 초과하는 CPU 시간을 가장하면
    같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 100ms를 반환하므로
    batch=64로 나눈 평균도 100ms > 1ms budget로 적색 확정.
    """
    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 100ms(100x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.1  # 100ms per call = 100x budget
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    actor, counterparty = uuid4(), uuid4()

    def _one_call() -> None:
        assert_actor_not_counterparty(actor, counterparty, action="perf.injection")

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_one_call, batch=64)
        assert sample.cpu_ms < 1.0, (
            f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"순수 도메인 로직은 1ms 미만이어야 함 (budget<1ms)"
        )


@pytest.mark.perf
def test_bulk_calls_complete_within_latency_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언: PLT-43은 매 DUAL 2차 서명·CM-5 활성화 호출마다 동기 경로에서
    실행되므로(순수 함수, I/O 없음), 만 번 호출이 평균 50us/call을 넘으면 이 primitive가
    실수로 I/O를 갖게 됐다는 회귀 신호로 본다."""
    actor, counterparty = uuid4(), uuid4()
    iterations = 10_000
    # 50us/call = 0.05ms per call (assert_within divides by batch)
    perf_budget.assert_within(
        lambda: assert_actor_not_counterparty(actor, counterparty, action="perf.bulk"),
        budget_ms=0.05,
        n=5,
        batch=iterations,
        label="segregation_of_duty.bulk",
    )
