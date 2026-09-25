"""DC-17 — Realtime market data fanout.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.2 DC-17.
Prerequisites: DC-9 `domain/entitlement/policy.py::allowed()`, PLT-06 `event_bus/envelope.py`.

Entitlement decisions are delegated entirely to DC-9 `allowed()` — this module
does not reimplement tier/scope comparison logic. A denied subscriber receives
nothing on its queue (zero deliveries), and the denial reason
(`EntitlementDenialReason`) is recorded as a metric label (fail-closed).

Backpressure: once a subscriber's per-queue cap (`max_queue_depth`, default
1000) is exceeded, the oldest item is dropped first (drop-oldest) —
`asyncio.Queue` by default rejects the new insertion instead (drop-newest), so
just before hitting the cap we manually evict the oldest item to implement
true drop-oldest semantics.

No new event bus or envelope type is introduced — PLT-06 `wrap()` builds the
`EventEnvelope` carrying the publish-time PLT-01 `RequestContext`
(trace_id/tenant_id) as-is into each subscriber's queue.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from src.core.event_bus.envelope import EventEnvelope, wrap
from src.core.observability import metric_names
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.foundation.market_data.domain.entitlement.policy import (
    EntitlementSubject,
    FeedRequest,
    allowed,
)

DEFAULT_MAX_QUEUE_DEPTH = 1000


@dataclass
class Subscription:
    """One subscription — the entitlement subject (`subject`), the requested feed
    (`feed`), and the delivery queue."""

    subscription_id: UUID
    subject: EntitlementSubject
    feed: FeedRequest
    queue: asyncio.Queue[EventEnvelope] = field(default_factory=asyncio.Queue)


def _topic_for(feed: FeedRequest) -> str:
    return (
        f"market_data.realtime.{feed.venue.value}.{feed.asset_class.value}."
        f"{feed.instrument_id}.{feed.timeframe.value}"
    )


class RealtimeFanout:
    """Fans out real-time market data to each subscriber's queue. Aside from
    entitlement decisions and backpressure drop counting, this performs pure
    routing only (serialization and transport layers live outside this module)."""

    def __init__(
        self,
        *,
        max_queue_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
        metrics: MetricsPort | None = None,
    ) -> None:
        if type(max_queue_depth) is not int or max_queue_depth <= 0:
            raise ValueError("max_queue_depth must be a positive integer")
        self._max_queue_depth = max_queue_depth
        # PLT-10 pattern — defaults to NullMetrics, no global singleton (caller injects it).
        self._metrics: MetricsPort = metrics if metrics is not None else NullMetrics()
        self._subscriptions: dict[UUID, Subscription] = {}

    def subscribe(self, subject: EntitlementSubject, feed: FeedRequest) -> Subscription:
        subscription = Subscription(
            subscription_id=uuid.uuid4(), subject=subject, feed=feed,
            queue=asyncio.Queue(maxsize=self._max_queue_depth),
        )
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def unsubscribe(self, subscription_id: UUID) -> None:
        self._subscriptions.pop(subscription_id, None)

    async def publish(self, feed: FeedRequest, payload: Any, *, as_of: datetime) -> None:
        """Delivers to every subscriber of `feed` after an entitlement check.

        `as_of` is the deterministic clock input required by DC-9 `allowed()`
        (the caller passes tz-aware UTC) — this function never reads the
        current time itself.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be a tz-aware datetime")
        envelope: EventEnvelope | None = None
        for subscription in list(self._subscriptions.values()):
            if subscription.feed != feed:
                continue
            entitlement = allowed(subscription.subject, feed, as_of)
            if not entitlement.allowed:
                # Entitlement's model_validator guarantees allowed=False -> reason
                # is present (policy.py), but mypy still sees it as Optional.
                reason = entitlement.reason.value if entitlement.reason is not None else "unknown"
                self._metrics.counter(
                    metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL,
                    labels={"reason": reason},
                )
                continue
            # Delayed permission from DC-9 must never expose live data.
            # There is no delay buffer here, so reject partial allowances.
            if entitlement.mode != "realtime":
                self._metrics.counter(
                    metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL,
                    labels={"reason": "REALTIME_REQUIRED"},
                )
                continue
            if envelope is None:
                envelope = wrap(_topic_for(feed), payload)
            self._deliver(subscription, envelope)
            self._metrics.counter(metric_names.MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL)

    def _deliver(self, subscription: Subscription, envelope: EventEnvelope) -> None:
        queue = subscription.queue
        if queue.qsize() >= self._max_queue_depth:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover — impossible when qsize>=max_queue_depth
                pass
            else:
                queue.task_done()
                self._metrics.counter(metric_names.MARKET_DATA_FANOUT_DROPPED_COUNT_TOTAL)
        queue.put_nowait(envelope)
