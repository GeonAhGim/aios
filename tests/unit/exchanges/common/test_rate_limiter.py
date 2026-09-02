"""L4-11 — rate_limiter 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11

가짜 clock/sleep을 주입해 실제 대기 없이 결정론적으로 검증한다(sleep 금지
규칙 — task-423 d3227c9 패턴).
"""
from __future__ import annotations

import pytest

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.rate_limiter import TokenBucket


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _make_bucket(rate: float, burst: float) -> tuple[TokenBucket, _FakeClock, list[float]]:
    clock = _FakeClock()
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.now += seconds

    bucket = TokenBucket(rate, burst, clock=clock, sleep=fake_sleep)
    return bucket, clock, sleep_calls


async def test_acquire_within_burst_does_not_wait() -> None:
    bucket, _clock, sleep_calls = _make_bucket(rate=10, burst=5)
    await bucket.acquire(3, timeout=1.0)
    assert sleep_calls == []


async def test_acquire_waits_for_refill_when_bucket_empty() -> None:
    bucket, clock, sleep_calls = _make_bucket(rate=10, burst=5)
    await bucket.acquire(5, timeout=5.0)  # 버킷 소진
    await bucket.acquire(5, timeout=5.0)  # 리필 대기 필요
    assert sleep_calls == [pytest.approx(0.5)]
    assert clock.now == pytest.approx(0.5)


async def test_acquire_raises_rate_limited_when_wait_exceeds_timeout() -> None:
    """초과 시 ExchangeError(RATE_LIMITED, retryable=True) — negative test."""
    bucket, _clock, sleep_calls = _make_bucket(rate=1, burst=1)
    await bucket.acquire(1, timeout=1.0)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(1, timeout=0.1)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert exc_info.value.retryable is True
    assert sleep_calls == []  # fail-fast: 실제로 기다리지 않고 즉시 실패


async def test_acquire_more_than_burst_rejected_immediately() -> None:
    bucket, _clock, sleep_calls = _make_bucket(rate=10, burst=5)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(6, timeout=100.0)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []
