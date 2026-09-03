"""4.3 / 4.4 / 4.5 — InProcessEventBus.

Spec: 05_communication_architecture_v1.2.md#§5.2, §5.5, §5.6;
08_test_plan_v1.2.md#§8.6 (백프레셔 정책)

audit_log 연동에 관한 편차: §5.5는 "모든 handler 예외는 audit_log에 자동
기록"을 요구하지만, 실제 audit_log 기록 유틸(작업트리 7.4)과 DB 세션 계층은
이 시점(작업트리 4번, 7번보다 먼저)에는 아직 없다. 이 클래스는 audit_sink
콜백을 주입받는 형태로 만들어 — 7.4가 준비되면 그 구현을 넘겨주기만 하면
되도록 설계했다(EventBus 자체를 인터페이스 뒤에 숨기는 §5.1 원칙과 동일한
방식). 기본값은 표준 logging으로 대체 기록한다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.event_bus.bus import EventBus, EventHandler
from src.core.event_bus.envelope import EventEnvelope, unwrap, wrap
from src.core.event_bus.policy import HandlerCriticality
from src.core.exceptions import EventHandlerError
from src.core.observability.context import bind

logger = logging.getLogger(__name__)

AuditSink = Callable[[dict[str, Any]], Awaitable[None]]

# §8.6 Draft 정책
DEFAULT_MAX_QUEUE_DEPTH = 1000
DEFAULT_BACKPRESSURE_SUSTAINED_SECONDS = 60.0
# §5.5 재시도 정책 Draft
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_INITIAL_DELAY_SECONDS = 1.0

# 큐가 지속적으로 가득 찬 상태를 상위 계층에 알리는 Event Bus 자체 토픽.
# market.distrust.entered 등 도메인 특화 토픽으로의 실제 전환은 이 이벤트를
# 구독하는 안전장치 계층(작업트리 9번)의 책임 — 범용 인프라 모듈인 Event Bus가
# 도메인 이름을 직접 알 필요는 없다.
BACKPRESSURE_SUSTAINED_TOPIC = "event_bus.queue.backpressure_sustained"
HANDLER_ESCALATED_TOPIC = "event_bus.handler.escalated"


async def _default_audit_sink(record: dict[str, Any]) -> None:
    logger.warning("audit_log 기록 유틸(7.4) 미연동 — 임시 로깅: %s", record)


class InProcessEventBus(EventBus):
    """Phase 1 구현체. asyncio.Queue 기반 topic별 큐 + 워커 코루틴.
    단일 프로세스 내에서만 동작 — 다중 프로세스/서버 분산은 Phase 4+ 확장 대상."""

    def __init__(
        self,
        *,
        max_queue_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
        backpressure_sustained_seconds: float = DEFAULT_BACKPRESSURE_SUSTAINED_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_initial_delay_seconds: float = DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._max_queue_depth = max_queue_depth
        self._backpressure_sustained_seconds = backpressure_sustained_seconds
        self._max_retries = max_retries
        self._retry_initial_delay_seconds = retry_initial_delay_seconds
        self._audit_sink: AuditSink = audit_sink or _default_audit_sink

        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._subscribers: dict[str, list[tuple[EventHandler, HandlerCriticality]]] = {}
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}
        self._queue_full_since: dict[str, float] = {}
        self._running = False

    def subscribe(
        self, topic: str, handler: EventHandler, *, criticality: HandlerCriticality
    ) -> None:
        self._subscribers.setdefault(topic, []).append((handler, criticality))
        if self._running:
            self._ensure_worker(topic)

    async def publish(self, topic: str, payload: Any) -> None:
        """PLT-06 — publish 시점의 PLT-01 컨텍스트를 봉투(`EventEnvelope`)에
        실어 큐에 넣는다. 워커는 이 봉투로 핸들러 실행 중 컨텍스트를 복원한다."""
        queue = self._get_or_create_queue(topic)
        if self._running:
            self._ensure_worker(topic)
        envelope = wrap(topic, payload)
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            await self._handle_backpressure(topic)
            return
        self._queue_full_since.pop(topic, None)

    async def start(self) -> None:
        self._running = True
        for topic in self._subscribers:
            self._ensure_worker(topic)

    async def stop(self) -> None:
        """Graceful shutdown — 각 워커가 처리 중이던 이벤트를 마치면 종료한다
        (아직 큐에 쌓여 있는 나머지 이벤트까지 전부 비우지는 않는다)."""
        self._running = False
        tasks = list(self._worker_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_tasks.clear()

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------

    def _get_or_create_queue(self, topic: str) -> asyncio.Queue[Any]:
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue(maxsize=self._max_queue_depth)
        return self._queues[topic]

    def _ensure_worker(self, topic: str) -> None:
        if topic in self._worker_tasks and not self._worker_tasks[topic].done():
            return
        queue = self._get_or_create_queue(topic)
        self._worker_tasks[topic] = asyncio.create_task(self._worker_loop(topic, queue))

    async def _worker_loop(self, topic: str, queue: asyncio.Queue[Any]) -> None:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not self._running:
                    return
                continue
            envelope, payload = unwrap(item)
            if envelope is None:  # 전환기 호환 — 봉투 없이 큐에 들어온 값
                envelope = wrap(topic, payload)
            try:
                for handler, criticality in list(self._subscribers.get(topic, [])):
                    await self._dispatch(topic, handler, criticality, envelope, payload)
            except Exception:  # noqa: BLE001 — #15 심층 방어: _dispatch가 이미 handler/
                # audit_sink 예외를 흡수하지만, 예기치 못한 예외까지 이 워커
                # 태스크를 죽이는 일은 절대 없어야 한다(그 순간부터 이 토픽의
                # 이후 이벤트가 전부 조용히 처리되지 않게 되므로).
                logger.exception("[%s] 워커 루프에서 예기치 못한 예외 — 워커는 계속 동작", topic)
            finally:
                queue.task_done()
            if not self._running:
                return

    async def _dispatch(
        self,
        topic: str,
        handler: EventHandler,
        criticality: HandlerCriticality,
        envelope: EventEnvelope,
        payload: Any,
    ) -> None:
        """PLT-06 — 봉투의 trace_id/tenant_id/actor_subject_id를 핸들러(재시도
        포함) 실행 동안만 바인딩한다. `bind()`는 컨텍스트 매니저 종료 시 항상
        이전 값으로 원복하므로, 핸들러가 예외를 던져도 컨텍스트가 새지 않는다."""
        with bind(
            trace_id=envelope.trace_id,
            tenant_id=envelope.tenant_id,
            actor_subject_id=envelope.actor_subject_id,
        ):
            try:
                await handler(payload)
            except Exception as exc:  # noqa: BLE001 — 의도적으로 모든 handler 예외를 포착
                if criticality == HandlerCriticality.SAFE:
                    await self._handle_safe_error(topic, handler, payload, exc)
                else:
                    await self._handle_critical_error(topic, handler, payload, exc)

    async def _handle_safe_error(
        self, topic: str, handler: EventHandler, payload: Any, exc: Exception
    ) -> None:
        """log_and_continue — 다른 handler에 영향 없이 계속 진행."""
        wrapped = EventHandlerError(f"[{topic}] SAFE handler 실패: {exc}")
        logger.warning("%s", wrapped, exc_info=exc)
        # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #15) 반영 — audit_sink 자체가
        # 실패해도(예: 실 DB 연동 후 커넥션 오류) 이 예외가 _worker_loop까지
        # 전파되면 해당 토픽의 워커 태스크 자체가 조용히 죽는다. 감사기록
        # 실패가 handler 처리 자체를 막지 않도록 여기서 흡수한다.
        try:
            await self._audit_sink(
                {
                    "actor_agent": "event_bus",
                    "action_type": "handler_error_safe",
                    "target_type": "topic",
                    "target_id": topic,
                    "decision_data": {"payload_repr": repr(payload), "error": str(exc)},
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("[%s] audit_sink 호출 실패(SAFE handler 오류 기록 중)", topic)

    async def _handle_critical_error(
        self, topic: str, handler: EventHandler, payload: Any, exc: Exception
    ) -> None:
        """escalate_and_retry — 지수 백오프로 최대 self._max_retries회 재시도.
        전부 실패하면 HANDLER_ESCALATED_TOPIC으로 격상(Circuit Breaker 연동은
        작업트리 9번에서 이 토픽을 구독하는 쪽의 책임)."""
        last_exc = exc
        for attempt in range(self._max_retries):
            delay = self._retry_initial_delay_seconds * (2**attempt)
            await asyncio.sleep(delay)
            try:
                await handler(payload)
                return  # 재시도 성공
            except Exception as retry_exc:  # noqa: BLE001
                last_exc = retry_exc

        wrapped = EventHandlerError(
            f"[{topic}] CRITICAL handler {self._max_retries}회 재시도 모두 실패: {last_exc}"
        )
        logger.error("%s", wrapped, exc_info=last_exc)
        try:
            await self._audit_sink(
                {
                    "actor_agent": "event_bus",
                    "action_type": "handler_error_critical_escalated",
                    "target_type": "topic",
                    "target_id": topic,
                    "decision_data": {
                        "payload_repr": repr(payload),
                        "error": str(last_exc),
                        "retries": self._max_retries,
                    },
                }
            )
        except Exception:  # noqa: BLE001 — #15와 동일 원칙, 워커 태스크를 죽이지 않는다
            logger.exception(
                "[%s] audit_sink 호출 실패(CRITICAL handler 에스컬레이션 기록 중)", topic
            )
        try:
            await self.publish(
                HANDLER_ESCALATED_TOPIC,
                {"topic": topic, "error": str(last_exc), "retries": self._max_retries},
            )
        except Exception:  # noqa: BLE001 — 격상 자체의 실패로 원본 흐름을 막지 않는다
            logger.exception("HANDLER_ESCALATED_TOPIC 발행 실패")

    async def _handle_backpressure(self, topic: str) -> None:
        """§8.6 — 신규 publish 거부 + WARNING 로그. drop-oldest는 하지 않는다."""
        logger.warning("Event Bus 큐 포화로 publish 거부: topic=%s", topic)
        now = time.monotonic()
        full_since = self._queue_full_since.setdefault(topic, now)
        # 메타 토픽 자신의 포화까지 재귀적으로 격상하면 무한 재귀가 될 수 있어 제외.
        sustained = now - full_since >= self._backpressure_sustained_seconds
        if topic != BACKPRESSURE_SUSTAINED_TOPIC and sustained:
            try:
                await self.publish(
                    BACKPRESSURE_SUSTAINED_TOPIC,
                    {"topic": topic, "sustained_seconds": now - full_since},
                )
            except Exception:  # noqa: BLE001
                logger.exception("BACKPRESSURE_SUSTAINED_TOPIC 발행 실패")
