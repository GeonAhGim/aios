"""DC-17 — application/realtime_fanout 단위 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.2 DC-17
DoD: (a) 미허가 구독자는 100건 발행에 정확히 0건 수신 + 거부 사유 코드가
남는다. (b) 큐 상한 초과 시 가장 오래된 것부터 버리고, 1500건 발행 시
drop=500을 정확히 단언한다(근사 비교 금지). (c) 계측은 `metric_names.py`
상수만 쓴다. (d) PLT-06 envelope의 trace_id/tenant_id를 전파한다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.event_bus.envelope import EventEnvelope
from src.core.observability import metric_names
from src.core.observability.context import bind
from src.data.models.base import AssetClass
from src.foundation.market_data.application.realtime_fanout import RealtimeFanout
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.entitlement.policy import (
    EntitlementDenialReason,
    EntitlementGrant,
    EntitlementSubject,
    FeedRequest,
)

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_SUBJECT = UUID("33333333-3333-3333-3333-333333333333")
_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise AssertionError("realtime_fanout은 observe를 쓰지 않는다")

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise AssertionError("realtime_fanout은 gauge를 쓰지 않는다")


def _feed(**overrides: Any) -> FeedRequest:
    fields: dict[str, Any] = dict(
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        instrument_id="BTC-USDT",
        timeframe=Timeframe.M1,
        want_realtime=True,
    )
    fields.update(overrides)
    return FeedRequest(**fields)


def _grant(**overrides: Any) -> EntitlementGrant:
    fields: dict[str, Any] = dict(
        tenant_id=_TENANT,
        subject_id=_SUBJECT,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        instrument_ids=frozenset({"BTC-USDT"}),
        timeframes=frozenset({Timeframe.M1}),
        realtime=True,
        delayed_seconds=900,
        expires_at=None,
    )
    fields.update(overrides)
    return EntitlementGrant(**fields)


def _subject(grants: tuple[EntitlementGrant, ...] = ()) -> EntitlementSubject:
    return EntitlementSubject(tenant_id=_TENANT, subject_id=_SUBJECT, grants=grants)


async def test_unauthorized_subscriber_receives_zero_of_100_and_leaves_denial_reason() -> None:
    fanout = RealtimeFanout(metrics=_SpyMetrics())
    feed = _feed()
    subscription = fanout.subscribe(_subject(grants=()), feed)

    for _ in range(100):
        await fanout.publish(feed, {"px": 1}, as_of=_AS_OF)

    assert subscription.queue.qsize() == 0
    spy = fanout._metrics
    assert isinstance(spy, _SpyMetrics)
    assert len(spy.counters) == 100
    for name, labels in spy.counters:
        assert name == metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL
        assert labels == {"reason": EntitlementDenialReason.NO_GRANT.value}


async def test_authorized_subscriber_receives_all_published_within_capacity() -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(metrics=spy)
    feed = _feed()
    subscription = fanout.subscribe(_subject(grants=(_grant(),)), feed)

    for i in range(10):
        await fanout.publish(feed, {"px": i}, as_of=_AS_OF)

    assert subscription.queue.qsize() == 10
    assert len(spy.counters) == 10
    published = metric_names.MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL
    assert all(name == published for name, _ in spy.counters)


async def test_backpressure_drops_oldest_and_counts_exact_overflow() -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(max_queue_depth=1000, metrics=spy)
    feed = _feed()
    subscription = fanout.subscribe(_subject(grants=(_grant(),)), feed)

    for i in range(1500):
        await fanout.publish(feed, {"seq": i}, as_of=_AS_OF)

    assert subscription.queue.qsize() == 1000
    drop_count = sum(
        1 for name, _ in spy.counters if name == metric_names.MARKET_DATA_FANOUT_DROPPED_COUNT_TOTAL
    )
    assert drop_count == 500

    # drop-oldest이므로 살아남은 항목은 마지막 1000건(seq 500..1499)이어야 한다.
    remaining_seqs = []
    while not subscription.queue.empty():
        envelope = subscription.queue.get_nowait()
        remaining_seqs.append(envelope.payload["seq"])
    assert remaining_seqs == list(range(500, 1500))


async def test_unrelated_feed_subscription_is_not_delivered_or_counted() -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(metrics=spy)
    subscribed_feed = _feed(instrument_id="BTC-USDT")
    other_feed = _feed(instrument_id="ETH-USDT")
    subscription = fanout.subscribe(_subject(grants=(_grant(),)), subscribed_feed)

    await fanout.publish(other_feed, {"px": 1}, as_of=_AS_OF)

    assert subscription.queue.qsize() == 0
    assert spy.counters == []


async def test_envelope_propagates_trace_id_and_tenant_id_from_publish_context() -> None:
    fanout = RealtimeFanout(metrics=_SpyMetrics())
    feed = _feed()
    subscription = fanout.subscribe(_subject(grants=(_grant(),)), feed)

    trace_id = uuid4()
    with bind(trace_id=trace_id, tenant_id=_TENANT):
        await fanout.publish(feed, {"px": 42}, as_of=_AS_OF)

    envelope: EventEnvelope = subscription.queue.get_nowait()
    assert envelope.trace_id == trace_id
    assert envelope.tenant_id == _TENANT
    assert envelope.payload == {"px": 42}


async def test_unsubscribe_stops_further_delivery() -> None:
    fanout = RealtimeFanout(metrics=_SpyMetrics())
    feed = _feed()
    subscription = fanout.subscribe(_subject(grants=(_grant(),)), feed)

    fanout.unsubscribe(subscription.subscription_id)
    await fanout.publish(feed, {"px": 1}, as_of=_AS_OF)

    assert subscription.queue.qsize() == 0


async def test_as_of_naive_datetime_is_rejected_via_dc9_policy() -> None:
    """DC-9 fail-closed 규칙(tz-naive 거부)이 그대로 전파된다 — 판정 재구현 없음의 증거."""
    fanout = RealtimeFanout(metrics=_SpyMetrics())
    feed = _feed()
    fanout.subscribe(_subject(grants=(_grant(),)), feed)

    try:
        await fanout.publish(feed, {"px": 1}, as_of=datetime(2026, 1, 1))
    except ValueError as exc:
        assert "tz-aware" in str(exc)
    else:
        raise AssertionError("naive datetime이 거부되지 않았다")


@pytest.mark.parametrize("want_realtime", [True, False])
async def test_delayed_allowance_never_delivers_live_data(want_realtime: bool) -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(metrics=spy)
    feed = _feed(want_realtime=want_realtime)
    subscription = fanout.subscribe(_subject((_grant(realtime=False),)), feed)
    for i in range(100):
        await fanout.publish(feed, {"seq": i}, as_of=_AS_OF)
    assert subscription.queue.empty()
    assert spy.counters == [
        (metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL, {"reason": "REALTIME_REQUIRED"})
    ] * 100


@pytest.mark.parametrize("depth", [0, -1, True, 1.5])
def test_invalid_capacity_is_rejected(depth: Any) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        RealtimeFanout(max_queue_depth=depth)


async def test_slow_subscriber_isolated_and_join_completes_after_drops() -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(max_queue_depth=1, metrics=spy)
    feed = _feed()
    slow = fanout.subscribe(_subject((_grant(),)), feed)
    fast = fanout.subscribe(_subject((_grant(),)), feed)
    for i in range(5):
        await fanout.publish(feed, {"seq": i}, as_of=_AS_OF)
        assert fast.queue.get_nowait().payload == {"seq": i}
        fast.queue.task_done()
    assert slow.queue.maxsize == 1
    assert slow.queue.get_nowait().payload == {"seq": 4}
    slow.queue.task_done()
    await asyncio.wait_for(slow.queue.join(), timeout=1)
    await asyncio.wait_for(fast.queue.join(), timeout=1)
    assert spy.counters.count((metric_names.MARKET_DATA_FANOUT_DROPPED_COUNT_TOTAL, None)) == 4
    assert spy.counters.count((metric_names.MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL, None)) == 10


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"tenant_id": uuid4()}, "TENANT_MISMATCH"),
        ({"subject_id": uuid4()}, "NO_GRANT"),
        ({"instrument_ids": frozenset({"ETH-USDT"})}, "OUT_OF_SCOPE"),
        ({"expires_at": _AS_OF}, "EXPIRED"),
    ],
)
async def test_policy_denials_enforced_at_delivery(overrides: dict[str, Any], reason: str) -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(metrics=spy)
    feed = _feed()
    denied = fanout.subscribe(_subject((_grant(**overrides),)), feed)
    permitted = fanout.subscribe(_subject((_grant(),)), feed)
    await fanout.publish(feed, {}, as_of=_AS_OF)
    assert denied.queue.empty()
    assert permitted.queue.qsize() == 1
    assert spy.counters == [
        (metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL, {"reason": reason}),
        (metric_names.MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL, None),
    ]


async def test_expiry_rechecked_on_each_publish() -> None:
    spy = _SpyMetrics()
    fanout = RealtimeFanout(max_queue_depth=1, metrics=spy)
    feed = _feed()
    expiry = _AS_OF + timedelta(seconds=1)
    subscription = fanout.subscribe(_subject((_grant(expires_at=expiry),)), feed)
    await fanout.publish(feed, {"seq": 0}, as_of=_AS_OF)
    await fanout.publish(feed, {"seq": 1}, as_of=expiry)
    assert subscription.queue.get_nowait().payload == {"seq": 0}
    assert spy.counters == [
        (metric_names.MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL, None),
        (metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL, {"reason": "EXPIRED"}),
    ]


async def test_naive_clock_rejected_without_subscribers() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        await RealtimeFanout().publish(_feed(), {}, as_of=datetime(2026, 1, 1))
