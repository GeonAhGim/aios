"""L4_compliance_and_regulatory_v1.0.md#9 CM-12 -- `notify_violations` unit tests.

task-2667 DoD mapping: negative (a) no rule codes -> no publish call, negative
(b) `publish=None` (not wired) -> no crash, negative (c) failure injection --
a raising `publish` is swallowed, does not propagate. Performance assertion:
notifying many violations stays well under the per-tenant batch budget
(`test_post_trade_batch.py`'s 0.5s/tenant floor) even before any I/O -- this
is the pure fan-out cost alone.
"""

from __future__ import annotations

import time
from datetime import date
from uuid import uuid4

import pytest

from src.foundation.mandates.application.notify_compliance_violation import notify_violations

_TENANT = uuid4()
_DATE = date(2026, 1, 5)


async def test_notify_violations_publishes_one_event_per_rule() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_publish(event_type: str, payload: dict[str, object]) -> None:
        calls.append((event_type, payload))

    await notify_violations(
        fake_publish,
        tenant_id=_TENANT,
        business_date=_DATE,
        rule_codes=["WASH_TRADE", "SHORT_SALE"],
    )

    assert [c[0] for c in calls] == [
        "execution.safety_block.applied",
        "execution.safety_block.applied",
    ]
    assert calls[0][1]["user_id"] == str(_TENANT)
    assert calls[0][1]["rule_code"] == "WASH_TRADE"
    assert calls[0][1]["reason"] == f"COMPLIANCE:WASH_TRADE:{_DATE.isoformat()}"
    assert calls[1][1]["rule_code"] == "SHORT_SALE"


async def test_notify_violations_no_rule_codes_does_not_publish() -> None:
    """negative -- a tenant with no newly-activated rule (e.g. an already
    ACTIVE block on rerun) must not be renotified."""
    calls = 0

    async def fake_publish(event_type: str, payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1

    await notify_violations(fake_publish, tenant_id=_TENANT, business_date=_DATE, rule_codes=[])

    assert calls == 0


async def test_notify_violations_no_publish_configured_is_a_noop() -> None:
    """negative -- callers that never wire a publisher (e.g. most existing
    unit tests calling `run_daily_post_trade_batch` directly) must not
    crash on `publish=None`."""
    await notify_violations(None, tenant_id=_TENANT, business_date=_DATE, rule_codes=["WASH_TRADE"])


async def test_notify_violations_swallows_publish_failure() -> None:
    """실패 주입 -- a raising publisher must not propagate: the
    `safety_control` block is already committed by the time this runs, so a
    notification outage cannot be allowed to fail the whole batch tick."""

    async def failing_publish(event_type: str, payload: dict[str, object]) -> None:
        raise RuntimeError("notification gateway unavailable")

    await notify_violations(
        failing_publish,
        tenant_id=_TENANT,
        business_date=_DATE,
        rule_codes=["WASH_TRADE", "SHORT_SALE"],
    )


@pytest.mark.perf
async def test_notify_violations_many_rules_stays_well_under_batch_budget() -> None:
    """성능 단언 -- pure fan-out cost (no I/O) for 200 rule hits must stay far
    under the 0.5s/tenant floor `test_batch_meets_tenant_throughput_floor`
    fixes for the whole batch tick (this is only the notify slice of it)."""

    async def fake_publish(event_type: str, payload: dict[str, object]) -> None:
        return None

    rule_codes = [f"RULE_{i}" for i in range(200)]
    start = time.perf_counter()
    await notify_violations(
        fake_publish, tenant_id=_TENANT, business_date=_DATE, rule_codes=rule_codes
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"200-rule notify fan-out took {elapsed:.3f}s"
