"""Unit tests for RiskAlertService (R-55) -- pure in-memory, injected clock
and gateway fake, no real DB/EventBus involved.

A subset of tests use the real production `NotificationGateway` +
`channel_policy` (with a fake asyncpg pool, no real DB) to reproduce actual
gateway-red failures instead of a generic fake exception -- see
`test_real_gateway_forced_channel_failure_is_swallowed_not_propagated`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from src.core.notifications.channel_policy import NotificationChannel
from src.core.notifications.gateway import NotificationGateway
from src.services.risk_alerting import LimitBreachEvent, RiskAlertService

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
LIMIT_1 = "SYMBOL:BTC-USDT:GROSS_NOTIONAL_PCT"
LIMIT_2 = "SYMBOL:ETH-USDT:GROSS_NOTIONAL_PCT"

_BASE = datetime(2026, 9, 8, 0, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeGateway:
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_on_call: bool = False
    exception: Exception = field(default_factory=lambda: RuntimeError("gateway unavailable"))
    delay: float = 0.0

    async def handle_event(self, payload: dict[str, Any]) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_on_call:
            raise self.exception
        self.calls.append(payload)


class _FakeAcquireCM:
    """Duck-types asyncpg's `pool.acquire()` async-context-manager just
    enough for `NotificationGateway._record` -- no real DB involved."""

    async def __aenter__(self) -> _FakeAcquireCM:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakePool:
    def acquire(self) -> _FakeAcquireCM:
        return _FakeAcquireCM()


def _clock(seconds_offsets: list[float]) -> Any:
    """Returns a clock callable that yields `_BASE + offset` on each call,
    one offset per call, in order -- avoids depending on real wall time."""
    it = iter(seconds_offsets)

    def _now() -> datetime:
        return _BASE + timedelta(seconds=next(it))

    return _now


def _event(*, tenant_id: UUID = TENANT_A, user_id: UUID = USER_A, hard: bool) -> LimitBreachEvent:
    return LimitBreachEvent(tenant_id=tenant_id, user_id=user_id, limit_id=LIMIT_1, hard=hard)


@pytest.mark.asyncio
async def test_suppression_boundary_holds_at_299s_and_lifts_at_301s() -> None:
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 299, 301]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=True))

    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_hard_breach_is_critical_severity() -> None:
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0]))

    await service.on_breach(_event(hard=True))

    assert gateway.calls[0]["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_soft_breach_is_warn_severity() -> None:
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0]))

    await service.on_breach(_event(hard=False))

    assert gateway.calls[0]["severity"] == "WARN"


@pytest.mark.asyncio
async def test_severity_is_not_swapped_by_a_faulty_gateway_assertion() -> None:
    """A fake gateway that (incorrectly) hard-codes the opposite severity
    must fail this assertion -- pins the mapping direction, not just that
    *some* severity string is present."""
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 0]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=False))

    assert gateway.calls[0]["severity"] == "CRITICAL"
    assert gateway.calls[1]["severity"] == "WARN"
    assert gateway.calls[0]["severity"] != gateway.calls[1]["severity"]


@pytest.mark.asyncio
async def test_tenant_suppression_does_not_swallow_other_tenants_alert() -> None:
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 0]))

    await service.on_breach(_event(tenant_id=TENANT_A, hard=True))
    await service.on_breach(_event(tenant_id=TENANT_B, hard=True))

    assert len(gateway.calls) == 2
    assert {c["tenant_id"] for c in gateway.calls} == {str(TENANT_A), str(TENANT_B)}


@pytest.mark.asyncio
async def test_gateway_failure_does_not_propagate_to_caller() -> None:
    gateway = FakeGateway(raise_on_call=True)
    service = RiskAlertService(gateway, _clock([0]))

    await service.on_breach(_event(hard=True))  # must not raise


@pytest.mark.asyncio
async def test_failed_send_is_not_cached_so_the_immediate_retry_is_not_suppressed() -> None:
    gateway = FakeGateway(raise_on_call=True)
    service = RiskAlertService(gateway, _clock([0, 0.001]))

    await service.on_breach(_event(hard=True))  # fails, swallowed
    gateway.raise_on_call = False
    await service.on_breach(_event(hard=True))  # retried a moment later

    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_different_limit_ids_do_not_share_suppression_window() -> None:
    """Negative/boundary: the suppression key must include limit_id, not
    just (tenant_id, severity) -- otherwise a breach on one limit would
    incorrectly suppress an unrelated limit's alert for the same tenant."""
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 0]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(
        LimitBreachEvent(tenant_id=TENANT_A, user_id=USER_A, limit_id=LIMIT_2, hard=True)
    )

    assert len(gateway.calls) == 2
    assert {c["limit_id"] for c in gateway.calls} == {LIMIT_1, LIMIT_2}


@pytest.mark.asyncio
async def test_gateway_timeout_does_not_propagate_to_caller_and_is_not_cached() -> None:
    """Failure injection with a distinct real-world exception type (not the
    generic RuntimeError used elsewhere) -- a hung notification backend
    times out rather than raising immediately; the fail-closed contract
    must hold for this failure mode too, and the failed attempt must not
    be cached (immediate retry must still go through)."""
    gateway = FakeGateway(raise_on_call=True, exception=asyncio.TimeoutError())
    service = RiskAlertService(gateway, _clock([0, 0.001]))

    await service.on_breach(_event(hard=True))  # times out, swallowed
    gateway.raise_on_call = False
    await service.on_breach(_event(hard=True))  # retried a moment later

    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_real_gateway_forced_channel_failure_is_swallowed_not_propagated() -> None:
    """Gate-red reproduction: uses the real production `NotificationGateway`
    + `channel_policy` (not a generic fake) so the failure this test
    injects is the actual one production hits -- `alert.triggered` forces
    IN_APP (`user_overridable=False`); with no IN_APP sender wired,
    `NotificationGateway.handle_event` raises a real `EventHandlerError`
    (the same red/DENY-equivalent signal production sees when a forced
    channel is unconfigured). `on_breach` must still not propagate it."""

    async def _email_sender(user_id: UUID, event_type: str, payload: dict[str, Any]) -> bool:
        return True

    gateway = NotificationGateway(
        _FakePool(),
        senders={NotificationChannel.EMAIL: _email_sender},
    )
    service = RiskAlertService(gateway, _clock([0]))

    await service.on_breach(
        _event(hard=True)
    )  # IN_APP unconfigured -> real EventHandlerError, must not raise


@pytest.mark.asyncio
async def test_concurrent_breaches_for_same_key_result_in_exactly_one_dispatch() -> None:
    """Adversarial concurrency proof (D3): 10 coroutines race `on_breach()`
    for the identical suppression key at the same instant. The gateway call
    sleeps briefly so the coroutines genuinely interleave (a real await
    point mid-dispatch, not just cooperative single-step ordering) -- the
    check-then-reserve section in `on_breach` must be atomic so exactly one
    dispatch survives the race."""
    gateway = FakeGateway(delay=0.01)
    fixed_now = _BASE

    def _fixed_clock() -> datetime:
        return fixed_now

    service = RiskAlertService(gateway, _fixed_clock)

    await asyncio.gather(*(service.on_breach(_event(hard=True)) for _ in range(10)))

    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_suppressed_path_dispatch_overhead_stays_under_5ms_p99() -> None:
    """Performance assertion: `on_breach` is called inline in the
    decision-recording path (see module docstring), so the common case --
    a duplicate breach inside the suppression window -- must return fast
    without touching the gateway. Measures wall-clock p99 over 200 calls."""
    gateway = FakeGateway()
    fixed_now = _BASE

    def _fixed_clock() -> datetime:
        return fixed_now

    service = RiskAlertService(gateway, _fixed_clock)
    await service.on_breach(_event(hard=True))  # establishes the reservation

    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        await service.on_breach(_event(hard=True))  # inside the window -> must be suppressed
        samples.append(time.perf_counter() - start)

    samples.sort()
    p99 = samples[int(len(samples) * 0.99)]
    assert p99 < 0.005, f"suppressed on_breach p99={p99 * 1000:.3f}ms exceeds 5ms budget"
    assert len(gateway.calls) == 1  # only the initial, unsuppressed send
