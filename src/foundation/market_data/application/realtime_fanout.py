"""DC-17 — Realtime market data fanout.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.2 DC-17.
선행: DC-9 `domain/entitlement/policy.py::allowed()`, PLT-06 `event_bus/envelope.py`.

Entitlement 판정은 전부 DC-9 `allowed()`에 위임한다 — 이 모듈은 tier/scope 비교
로직을 재구현하지 않는다. 거부된 구독자는 큐에 아무것도 받지 못하고(0건 수신),
거부 사유(`EntitlementDenialReason`)가 메트릭 라벨로 남는다(fail-closed).

Backpressure는 구독자별 큐 상한(`max_queue_depth`, 기본 1000)을 넘으면 가장
오래된 항목부터 버린다(drop-oldest) — `asyncio.Queue`는 표준적으로 신규 투입을
거부(drop-newest)하므로, 상한 직전에 수동으로 가장 오래된 항목을 비워 정확한
drop-oldest 의미를 구현한다.

새 이벤트 버스나 새 봉투를 만들지 않는다 — PLT-06 `wrap()`이 publish 시점의
PLT-01 `RequestContext`(trace_id/tenant_id)를 그대로 실어 각 구독자 큐에 넣는
`EventEnvelope`를 만든다.
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
    """구독 1건 — 판정 대상(`subject`)과 원하는 피드(`feed`), 전달 큐."""

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
    """실시간 시세를 구독자별 큐로 팬아웃한다. 권한 판정과 backpressure drop
    카운팅을 제외하면 순수 라우팅만 수행한다(직렬화·전송 계층은 이 모듈 밖)."""

    def __init__(
        self,
        *,
        max_queue_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._max_queue_depth = max_queue_depth
        # PLT-10 패턴 — 기본값 NullMetrics, 전역 싱글턴 미사용(호출부 주입).
        self._metrics: MetricsPort = metrics if metrics is not None else NullMetrics()
        self._subscriptions: dict[UUID, Subscription] = {}

    def subscribe(self, subject: EntitlementSubject, feed: FeedRequest) -> Subscription:
        subscription = Subscription(subscription_id=uuid.uuid4(), subject=subject, feed=feed)
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def unsubscribe(self, subscription_id: UUID) -> None:
        self._subscriptions.pop(subscription_id, None)

    async def publish(self, feed: FeedRequest, payload: Any, *, as_of: datetime) -> None:
        """`feed`를 구독 중인 대상 전원에게 권한 판정 후 전달한다.

        `as_of`는 DC-9 `allowed()`가 요구하는 결정론적 시계 입력이다(호출자가
        tz-aware UTC로 넘긴다) — 이 함수는 현재 시각을 스스로 읽지 않는다.
        """
        envelope: EventEnvelope | None = None
        for subscription in list(self._subscriptions.values()):
            if subscription.feed != feed:
                continue
            entitlement = allowed(subscription.subject, feed, as_of)
            if not entitlement.allowed:
                # Entitlement의 model_validator가 allowed=False -> reason 존재를
                # 보장하지만(policy.py), mypy 관점에서는 여전히 Optional이다.
                reason = entitlement.reason.value if entitlement.reason is not None else "unknown"
                self._metrics.counter(
                    metric_names.MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL,
                    labels={"reason": reason},
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
            except asyncio.QueueEmpty:  # pragma: no cover — qsize>=max_queue_depth(>0)면 불가
                pass
            else:
                self._metrics.counter(metric_names.MARKET_DATA_FANOUT_DROPPED_COUNT_TOTAL)
        queue.put_nowait(envelope)
