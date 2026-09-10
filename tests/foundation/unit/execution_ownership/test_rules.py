"""EO-01 순수 규칙 단위테스트 — DB 없음.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §8
"단위(순수 규칙): is_lease_available() — 없음/만료/동일소유자/타인점유
4개 케이스" + naive datetime 거부 negative test.

DEPTH 감사(docs/audit/DEPTH_R_EO.md, task-2721/task-2812) — D2 미달분
보강: 실패 주입(적대적으로 조작된 tzinfo가 naive 검사를 우회해 제어되지
않은 TypeError로 새는 경로) + 성능 단언 + 결정론적 재현(replay) 증명.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from src.foundation.execution_ownership.domain.models import ExecutionLease
from src.foundation.execution_ownership.domain.rules import is_lease_available

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


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


def test_is_lease_available_expired_lease():
    existing = _lease("worker-b", expires_at=_NOW - timedelta(seconds=1))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is True


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


def test_is_lease_available_hot_path_performance():
    # 성능 단언: EO-03 스케줄러가 매 tick마다 후보군 전체에 대해 이 순수
    # 함수를 호출할 수 있어야 하므로(§4.1), 10,000회 호출이 100ms 내로
    # 끝나야 한다 — I/O 없는 순수 비교 연산이라는 계약의 실측 증명.
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    iterations = 10_000
    started = time.perf_counter()
    for _ in range(iterations):
        is_lease_available(existing, now=_NOW, requesting_owner="worker-a")
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1, f"{iterations}회 호출에 {elapsed:.4f}s — 순수 함수치고 너무 느리다"


def test_is_lease_available_is_deterministic_across_repeated_calls():
    # 리플레이 증명: 동일 입력을 반복 호출해도 항상 동일한 판정이 나와야
    # §5.1 SQL과의 일치 보증(모듈 docstring)이 성립한다 — 순수 함수이므로
    # 숨은 가변 상태가 없음을 반복 호출로 실증한다.
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    results = {
        is_lease_available(existing, now=_NOW, requesting_owner="worker-a") for _ in range(1_000)
    }
    assert results == {False}
