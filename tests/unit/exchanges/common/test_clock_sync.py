"""L4-11 — clock_sync 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: 왕복 보정, skew 초과 차단.
"""
from __future__ import annotations

import pytest

from src.exchanges.common.clock_sync import ServerClock
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind


class _FakeClock:
    """호출할 때마다 미리 정해진 값을 순서대로 반환한다(t0, t1, t0, t1, ...)."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = list(ticks)
        self._idx = 0

    def __call__(self) -> float:
        value = self._ticks[self._idx]
        self._idx += 1
        return value


async def test_sync_applies_half_round_trip_correction() -> None:
    # t0=1000, 서버가 1100을 보고함, t1=1040 → round_trip=40, 절반=20
    # estimated_server_now = 1100 + 20 = 1120, offset = 1120 - 1040 = 80
    clock = _FakeClock([1000.0, 1040.0])
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 1100

    await server_clock.sync(fetch_server_ms)
    assert server_clock.offset_ms == pytest.approx(80.0)
    assert server_clock.last_sync_at == pytest.approx(1040.0)


async def test_now_ms_uses_offset() -> None:
    clock_calls = [1000.0, 1040.0, 5000.0]
    clock = _FakeClock(clock_calls)
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 1100

    await server_clock.sync(fetch_server_ms)
    assert server_clock.now_ms() == round(5000.0 + 80.0)


async def test_skew_exceeding_max_raises_clock_skew_before_signing() -> None:
    """max_skew_ms 초과 시 서명 전 차단 — negative test."""
    clock = _FakeClock([0.0, 0.0])  # round_trip=0이므로 offset == server-local 차이
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 5000  # local=0이므로 offset=5000ms, max_skew_ms=1000 초과

    with pytest.raises(ExchangeError) as exc_info:
        await server_clock.sync(fetch_server_ms)
    assert exc_info.value.kind == ExchangeErrorKind.CLOCK_SKEW
    assert exc_info.value.retryable is False
    # 오프셋은 raise 전에 이미 갱신돼 있어야 진단에 쓸 수 있다.
    assert server_clock.offset_ms == pytest.approx(5000.0)


async def test_skew_within_bound_does_not_raise() -> None:
    clock = _FakeClock([0.0, 0.0])
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 500

    await server_clock.sync(fetch_server_ms)
    assert server_clock.offset_ms == pytest.approx(500.0)
