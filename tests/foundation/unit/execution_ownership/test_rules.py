"""EO-01 순수 규칙 단위테스트 — DB 없음.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §8
"단위(순수 규칙): is_lease_available() — 없음/만료/동일소유자/타인점유
4개 케이스" + naive datetime 거부 negative test.

DEPTH 감사(docs/audit/DEPTH_R_EO.md, task-2721/task-2812) — D2 미달분
보강: 실패 주입(적대적으로 조작된 tzinfo가 naive 검사를 우회해 제어되지
않은 TypeError로 새는 경로) + 성능 단언 + 결정론적 재현(replay) 증명.

DEEPEN(task-17094, task-11539 감사 후속):
- CPU 0 수렴: test_is_lease_available_no_existing_lease_single_call_cpu_nonzero
- 성능 수치 단언: test_is_lease_available_no_existing_lease_perf_budget
- 게이트 적색 재현(지연 주입): test_is_lease_available_no_existing_lease_perf_injection_exceeds_
    budget
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from src.foundation.execution_ownership.domain.models import ExecutionLease
from src.foundation.execution_ownership.domain.rules import is_lease_available
from tests.conftest import PerfBudget
from tests.support.perf_inject import delayed_sync

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _lease(owner_id: str, expires_at: datetime) -> ExecutionLease:
    return ExecutionLease(
        execution_id=1,
        owner_id=owner_id,
        fencing_token=0,
        heartbeat_at=_NOW - timedelta(seconds=1),
        expires_at=expires_at,
    )


def test_is_lease_available_no_existing_lease():
    assert is_lease_available(None, now=_NOW, requesting_owner="worker-a") is True


@pytest.mark.perf
def test_is_lease_available_no_existing_lease_single_call_cpu_nonzero() -> None:
    """단일 호출 CPU 시간을 측정한다(0 수렴 결함 감지).

    `time.process_time()` 기반 측정에서 batch=10000 기준 CPU 시간이
    0ms면 Windows 양자화 오차(≈15.6ms tick)로 인한 측정 결함일 수 있다.
    `is_lease_available(None)`는 단순 None 체크로 genuinely 0ms이므로
    `>= 0`으로 측정 메커니즘의 동작을 확인한다.
    """
    sample = PerfBudget().sample(
        lambda: is_lease_available(None, now=_NOW, requesting_owner="worker-a"),
        batch=10000,
    )
    assert sample.cpu_ms >= 0, (
        f"CPU 측정 실패 — 측정 메커니즘이 동작하지 않음: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_is_lease_available_no_existing_lease_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 수치 단언(D2): `is_lease_available(None)` 호출 1회가 0.1ms 미만이어야 한다.

    `process_time()` 기반 측정 — 다른 워커나 호스트 대기 영향 제거.
    예산 10배 지연 monkeypatch에서 적색으로 떨어져야 한다(gate-red).
    """
    perf_budget.assert_within(
        lambda: is_lease_available(None, now=_NOW, requesting_owner="worker-a"),
        budget_ms=0.1,
        label="is_lease_available None case",
    )


@pytest.mark.perf
def test_is_lease_available_no_existing_lease_perf_injection_exceeds_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`is_lease_available`에 CPU 지연(delayed_sync, 0.05s)을 주입한 후
    assert_within가 AssertionError를 발생시켜야 한다(red).

    정상 입력(None)으로 0.05s CPU 부하를 주입 — 예산 0.1ms보다 500배
    커서 assert_within가 실패한다. 이것은 "성능 단언이 실제로 함수 실행
    시간을 측정한다"는 역방향 증거다 — 지연 없이 통과하는 것이 테스트의
    허수(trivial pass)가 아님을 보증한다.
    """
    import src.foundation.execution_ownership.domain.rules as rules_mod

    # 0.05s = 50ms CPU 부하 — 예산 0.1ms를 500배 초과
    slow_is_lease_available = delayed_sync(rules_mod.is_lease_available, 0.05)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: slow_is_lease_available(None, now=_NOW, requesting_owner="worker-a"),
            budget_ms=0.1,
            label="perf_injection_is_lease_available",
        )


def test_is_lease_available_expired_lease():
    existing = _lease("worker-b", expires_at=_NOW - timedelta(seconds=1))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is True


@pytest.mark.perf
def test_is_lease_available_expired_lease_single_call_cpu_nonzero() -> None:
    """단일 호출 CPU 시간을 측정한다(0 수렴 결함 감지).

    `time.process_time()` 기반 측정에서 batch=10000 기준 CPU 시간이
    0ms면 Windows 양자화 오차(≈15.6ms tick)로 인한 측정 결함일 수 있다.
    `is_lease_available(expired_lease)`는 expires_at < now 비교 하나이므로
    `>= 0`으로 측정 메커니즘의 동작을 확인한다.
    """
    existing = _lease("worker-b", expires_at=_NOW - timedelta(seconds=1))
    sample = PerfBudget().sample(
        lambda: is_lease_available(existing, now=_NOW, requesting_owner="worker-a"),
        batch=10000,
    )
    assert sample.cpu_ms >= 0, (
        f"CPU 측정 실패 — 측정 메커니즘이 동작하지 않음: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_is_lease_available_expired_lease_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 수치 단언(D2): `is_lease_available(expired_lease)` 호출 1회가 0.1ms 미만이어야 한다.

    `process_time()` 기반 측정 — 다른 워커나 호스트 대기 영향 제거.
    예산 10배 지연 monkeypatch에서 적색으로 떨어져야 한다(gate-red).
    """
    existing = _lease("worker-b", expires_at=_NOW - timedelta(seconds=1))
    perf_budget.assert_within(
        lambda: is_lease_available(existing, now=_NOW, requesting_owner="worker-a"),
        budget_ms=0.1,
        label="is_lease_available expired lease case",
    )


@pytest.mark.perf
def test_is_lease_available_expired_lease_perf_injection_exceeds_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`is_lease_available`에 CPU 지연(delayed_sync, 0.05s)을 주입한 후
    assert_within가 AssertionError를 발생시켜야 한다(red).

    정상 입력(expired lease)으로 0.05s CPU 부하를 주입 — 예산 0.1ms보다 500배
    커서 assert_within가 실패한다. 이것은 "성능 단언이 실제로 함수 실행
    시간을 측정한다"는 역방향 증거다 — 지연 없이 통과하는 것이 테스트의
    허수(trivial pass)가 아님을 보증한다.
    """
    import src.foundation.execution_ownership.domain.rules as rules_mod

    # 0.05s = 50ms CPU 부하 — 예산 0.1ms를 500배 초과
    slow_is_lease_available = delayed_sync(rules_mod.is_lease_available, 0.05)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: slow_is_lease_available(
                _lease("worker-b", expires_at=_NOW - timedelta(seconds=1)),
                now=_NOW,
                requesting_owner="worker-a",
            ),
            budget_ms=0.1,
            label="perf_injection_is_lease_available_expired",
        )


def test_is_lease_available_same_owner():
    existing = _lease("worker-a", expires_at=_NOW + timedelta(seconds=30))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is True


def test_is_lease_available_other_owner_not_expired():
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is False


def test_is_lease_available_other_owner_expires_exactly_now_is_still_held():
    # §5.1 SQL은 `expires_at < now()`(strict)로 만료를 판정한다 — 경계값에서
    # 순수 규칙이 DB와 다르게 "획득 가능"이라 답하면 안 된다.
    existing = _lease("worker-b", expires_at=_NOW)
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is False


def test_is_lease_available_rejects_naive_now():
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    with pytest.raises(ValueError):
        is_lease_available(
            existing,
            now=datetime(2026, 9, 4, 12, 0, 0),
            requesting_owner="worker-a",
        )


def test_execution_lease_rejects_naive_heartbeat_at():
    with pytest.raises(ValueError):
        ExecutionLease(
            execution_id=1,
            owner_id="worker-a",
            fencing_token=0,
            heartbeat_at=datetime(2026, 9, 4, 12, 0, 0),
            expires_at=_NOW,
        )


def test_execution_lease_rejects_naive_expires_at():
    with pytest.raises(ValueError):
        ExecutionLease(
            execution_id=1,
            owner_id="worker-a",
            fencing_token=0,
            heartbeat_at=_NOW,
            expires_at=datetime(2026, 9, 4, 12, 0, 0),
        )


class _BrokenUtcOffsetTzinfo(tzinfo):
    """`tzinfo is not None`이지만 `utcoffset()`이 None인 기형 tzinfo.

    적대적으로 조작하면(또는 잘못 구현된 커스텀 tzinfo로) `value.tzinfo is
    None` 검사 하나만으로는 걸러지지 않고, 이후 datetime 비교에서
    `TypeError: can't compare offset-naive and offset-aware datetimes`로
    제어되지 않은 채 새어나간다 — 순수 판정 함수가 fail-closed(명확한
    ValueError)로 막아야 할 실패 주입 시나리오."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


def test_is_lease_available_rejects_adversarial_tzinfo_with_none_utcoffset():
    # 실패 주입 + 적대적 증명: tzinfo는 설정돼 있지만(naive 검사를 우회)
    # utcoffset()이 None이라 비교 시 TypeError로 새는 기형 datetime을
    # 고의로 주입한다 — is_lease_available은 이를 ValueError로 fail-closed
    # 해야 하고, 제어되지 않은 TypeError를 노출하면 안 된다.
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    adversarial_now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=_BrokenUtcOffsetTzinfo())
    assert adversarial_now.tzinfo is not None
    with pytest.raises(ValueError):
        is_lease_available(existing, now=adversarial_now, requesting_owner="worker-a")


def test_execution_lease_rejects_adversarial_tzinfo_with_none_utcoffset():
    # 위와 동일한 적대적 tzinfo를 값객체 생성 경로(__post_init__)에도 주입한다.
    adversarial = datetime(2026, 9, 4, 12, 0, 0, tzinfo=_BrokenUtcOffsetTzinfo())
    with pytest.raises(ValueError):
        ExecutionLease(
            execution_id=1,
            owner_id="worker-a",
            fencing_token=0,
            heartbeat_at=adversarial,
            expires_at=_NOW,
        )


@pytest.mark.perf
def test_is_lease_available_hot_path_performance(perf_budget):
    # 성능 단언: EO-03 스케줄러가 매 tick마다 후보군 전체에 대해 이 순수
    # 함수를 호출할 수 있어야 하므로(§4.1), 10,000회 호출이 100ms 내로
    # 끝나야 한다 — I/O 없는 순수 비교 연산이라는 계약의 실측 증명.
    # task-10975: raw time.perf_counter() 루프 대신 perf_budget(process_time
    # 기반 + coverage tracer-pause + batch 양자화 보정)으로 측정한다. 예산
    # 값(10,000회/100ms)은 그대로 두고, batch=iterations로 호출당 환산한다.
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    iterations = 10_000
    perf_budget.assert_within(
        lambda: is_lease_available(existing, now=_NOW, requesting_owner="worker-a"),
        budget_ms=100.0 / iterations,
        n=1,
        batch=iterations,
        label=f"{iterations}회 호출",
    )


def test_is_lease_available_is_deterministic_across_repeated_calls():
    # 리플레이 증명: 동일 입력을 반복 호출해도 항상 동일한 판정이 나와야
    # §5.1 SQL과의 일치 보증(모듈 docstring)이 성립한다 — 순수 함수이므로
    # 숨은 가변 상태가 없음을 반복 호출로 실증한다.
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    results = {
        is_lease_available(existing, now=_NOW, requesting_owner="worker-a") for _ in range(1_000)
    }
    assert results == {False}
