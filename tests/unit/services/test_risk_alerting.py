"""Unit tests for RiskAlertService (R-55) -- pure in-memory, injected clock
and gateway fake, no real DB/EventBus involved."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from src.services.risk_alerting import LimitBreachEvent, RiskAlertService

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
LIMIT_1 = "SYMBOL:BTC-USDT:GROSS_NOTIONAL_PCT"

_BASE = datetime(2026, 9, 8, 0, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeGateway:
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_on_call: bool = False

    async def handle_event(self, payload: dict[str, Any]) -> None:
        if self.raise_on_call:
            raise RuntimeError("gateway unavailable")
        self.calls.append(payload)


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
