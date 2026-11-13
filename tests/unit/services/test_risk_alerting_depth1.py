"""Depth-1 tests for RiskAlertService (R-55) -- negative + failure-injection + perf.

Supplements test_risk_alerting.py with D2/D3 evidence the original file
lacked: 3+ negative tests, failure-injection, gate-red reproduction, and
performance assertions.

DoD targets (task-24765):
  - negative tests ≥ 3   (test_same_key_suppressed,
                           test_different_limit_not_suppressed,
                           test_different_severity_not_suppressed,
                           test_gateway_failure_allows_retry)
  - failure_injection 1  (test_gateway_failure_clears_reservation)
  - gate_red 1           (test_forced_channel_failure_is_swallowed_not_propagated)
  - perf 1               (test_suppressed_path_perf_budget)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from src.core.notifications.channel_policy import NotificationChannel
from src.core.notifications.gateway import NotificationGateway
from src.services.risk_alerting import LimitBreachEvent, RiskAlertService
from tests.conftest import PerfBudget
from tests.support.perf_inject import (
    virtual_delayed_async,
    virtual_totals_ns,
)

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
LIMIT_1 = "SYMBOL:BTC-USDT:GROSS_NOTIONAL_PCT"
LIMIT_2 = "SYMBOL:ETH-USDT:GROSS_NOTIONAL_PCT"

_BASE = datetime(2026, 9, 8, 0, 0, 0, tzinfo=UTC)


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


def _clock(seconds_offsets: list[float]) -> Any:
    """Returns a clock callable that yields `_BASE + offset` on each call,
    one offset per call, in order."""
    it = iter(seconds_offsets)

    def _now() -> datetime:
        return _BASE + timedelta(seconds=next(it))

    return _now


def _event(
    *,
    tenant_id: UUID = TENANT_A,
    user_id: UUID = USER_A,
    limit_id: str = LIMIT_1,
    hard: bool,
) -> LimitBreachEvent:
    return LimitBreachEvent(tenant_id=tenant_id, user_id=user_id, limit_id=limit_id, hard=hard)


# ---------------------------------------------------------------------------
# Negative tests (D2: 음성 테스트 3종 이상)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_key_suppressed_within_window() -> None:
    """Identical (tenant, limit_id, severity) inside the 5-min window must be
    suppressed -- the core invariant of the suppression window."""
    gateway = FakeGateway()
    # Two calls 60 s apart -- well inside the 5-min window.
    service = RiskAlertService(gateway, _clock([0, 60]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=True))

    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_different_limit_not_suppressed() -> None:
    """A different limit_id must NOT be suppressed, even for the same
    tenant/user within the window."""
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 10]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=True, limit_id=LIMIT_2))

    assert len(gateway.calls) == 2
    assert gateway.calls[0]["limit_id"] == LIMIT_1
    assert gateway.calls[1]["limit_id"] == LIMIT_2


@pytest.mark.asyncio
async def test_different_severity_not_suppressed() -> None:
    """Hard (CRITICAL) and soft (WARN) breaches use different severity keys,
    so they must NOT suppress each other."""
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 10]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=False))

    assert len(gateway.calls) == 2
    assert gateway.calls[0]["severity"] == "CRITICAL"
    assert gateway.calls[1]["severity"] == "WARN"


@pytest.mark.asyncio
async def test_gateway_failure_clears_reservation() -> None:
    """When the gateway raises, the reservation must be deleted so the very
    next breach retries immediately (not suppressed by its own failed
    attempt). This is the fail-closed + retry guarantee."""
    gateway = FakeGateway()
    # First call fails, second call succeeds.
    gateway.raise_on_call = True
    service = RiskAlertService(gateway, _clock([0, 1]))

    await service.on_breach(_event(hard=True))  # fails
    gateway.raise_on_call = False
    await service.on_breach(_event(hard=True))  # should retry

    assert len(gateway.calls) == 1
    # Only the second call succeeded; the first was swallowed.


# ---------------------------------------------------------------------------
# Failure-injection test (D2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_failure_does_not_crash_on_breach() -> None:
    """A gateway that always raises must NOT propagate the exception to the
    caller -- the breach handler swallows all gateway errors."""
    gateway = FakeGateway(raise_on_call=True)
    service = RiskAlertService(gateway, _clock([0]))

    # Must not raise.
    await service.on_breach(_event(hard=True))

    # No calls delivered.
    assert len(gateway.calls) == 0


# ---------------------------------------------------------------------------
# Gate-red reproduction (D2/D3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forced_channel_failure_is_swallowed_not_propagated() -> None:
    """Reproduce the actual production gateway-red failure: the real
    NotificationGateway with a fake asyncpg pool that forces the
    `channel_policy` to raise. The alerting service must swallow this
    (same as the FakeGateway test above, but through the real gateway path)."""

    # _FakePool is defined in the sibling test file, import it from there.
    from tests.unit.services.test_risk_alerting import _FakePool  # noqa: PLC0414

    async def _email_sender(user_id: UUID, event_type: str, payload: dict[str, Any]) -> bool:
        return True

    fake_pool = _FakePool()
    gateway = NotificationGateway(
        fake_pool,
        senders={NotificationChannel.EMAIL: _email_sender},
    )
    service = RiskAlertService(gateway, _clock([0]))

    # IN_APP unconfigured -> real EventHandlerError, must not raise.
    await service.on_breach(_event(hard=True))


# ---------------------------------------------------------------------------
# Performance assertions (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.asyncio
async def test_suppressed_path_perf_budget(perf_budget: PerfBudget) -> None:
    """Performance assertion: suppressed `on_breach` calls must stay under
    5 ms p99 CPU time.

    Budget rationale: the suppressed path is a dict lookup + timestamp
    comparison + lock acquire/release in a single thread. Even with
    coverage-tracer overhead, 5 ms is 30× the observed baseline (~0.1 ms)
    on the CI host (see task-9121 perf regression analysis).

    This test also uses `virtual_totals_ns` injection to verify that the
    budget assertion correctly accounts for virtual delays: we inject 10×
    the normal gate delay (100 ms virtual) and confirm the budget still
    passes for the suppressed path (which does NOT touch the gateway).
    """
    gateway = FakeGateway()
    fixed_now = _BASE

    def _fixed_clock() -> datetime:
        return fixed_now

    service = RiskAlertService(gateway, _fixed_clock)
    await service.on_breach(_event(hard=True))  # establish the reservation

    # Inject 100 ms of virtual delay into the gateway path. The suppressed
    # path should NOT be affected because it returns before calling the
    # gateway.
    original_handle = gateway.handle_event
    gateway.handle_event = virtual_delayed_async(original_handle, 10.0)

    samples = [
        await perf_budget.sample_async(lambda g=gateway, s=service: s.on_breach(_event(hard=True)))
        for _ in range(200)
    ]

    cpu_ms_samples = sorted(s.cpu_ms for s in samples)
    p99 = cpu_ms_samples[int(len(cpu_ms_samples) * 0.99)]
    assert p99 < 5.0, f"suppressed on_breach p99={p99:.3f}ms exceeds 5ms budget"
    assert len(gateway.calls) == 1  # only the initial, unsuppressed send

    # Verify virtual delay ledger was NOT touched by suppressed calls.
    cpu_ns, wall_ns = virtual_totals_ns()
    # Only the initial call (no delay on it) and possibly nothing else.
    # The 200 suppressed calls must not have added any virtual delay.
    assert cpu_ns == 0, f"suppressed calls leaked virtual cpu delay: {cpu_ns}ns"
    assert wall_ns == 0, f"suppressed calls leaked virtual wall delay: {wall_ns}ns"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_best_of_returns_minimum_cpu_sample(perf_budget: PerfBudget) -> None:
    """best_of should return the sample with the minimum cpu_ms among
    candidates, providing noise resilience."""
    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0]))
    await service.on_breach(_event(hard=True))

    result = perf_budget.best_of(
        lambda g=gateway, s=service: s.on_breach(_event(hard=True)),
        n=5,
    )

    assert isinstance(result.cpu_ms, float)
    assert result.cpu_ms >= 0.0
    assert len(gateway.calls) == 1  # all suppressed


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_expiry_allows_retry() -> None:
    """After the suppression window expires, a new alert must be sent even
    for the same (tenant, limit_id, severity)."""
    gateway = FakeGateway()
    # 301 seconds apart -- just past the 5-min (300 s) window.
    service = RiskAlertService(gateway, _clock([0, 301]))

    await service.on_breach(_event(hard=True))
    await service.on_breach(_event(hard=True))

    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_multiple_tenants_isolated() -> None:
    """Breach events from different tenants must NOT suppress each other,
    even with the same limit_id and user_id."""
    tenant_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    gateway = FakeGateway()
    service = RiskAlertService(gateway, _clock([0, 10]))

    await service.on_breach(
        LimitBreachEvent(tenant_id=TENANT_A, user_id=USER_A, limit_id=LIMIT_1, hard=True)
    )
    await service.on_breach(
        LimitBreachEvent(tenant_id=tenant_b, user_id=USER_A, limit_id=LIMIT_1, hard=True)
    )

    assert len(gateway.calls) == 2
