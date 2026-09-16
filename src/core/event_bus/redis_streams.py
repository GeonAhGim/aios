"""4.7 (M2-8 Phase2) — RedisStreamsEventBus.

Spec: ADR-2026-09-09-B (M2-8) — replaces the single-process `asyncio.Queue`
in `src/core/event_bus/in_process.py` behind the `EventBus` port (bus.py)
with a Redis Streams persistent backend. `InProcessEventBus` is not being
replaced — it stays for tests (§5.1 port/adapter principle: subscriber code
does not change regardless of which implementation is wired in).

Envelope wire encoding lives in `redis_streams_codec.py` and the SAFE/
CRITICAL handler-error policy lives in `redis_streams_dispatch.py` — both
split out so each file stays under the architecture guard's 300-line cap
(policy/immutable.md P6); this module owns the Redis Streams transport
itself (consumer groups, crash recovery, idempotency).

Design notes:
  - One topic = one Redis Stream (`STREAM_KEY_PREFIX + topic`). `publish()`
    only does XADD — the stream is created automatically if it does not
    exist yet.
  - Every worker belongs to the same consumer group (`consumer_group`).
    `start()` tries `XGROUP CREATE ... id=0 MKSTREAM` per topic (BUSYGROUP
    is ignored if the group already exists) — id=0 means a brand-new group
    will also see every entry the stream already held, so a late subscriber
    or a restarted process never misses events that accumulated while it
    was away.
  - Crash recovery: every worker-loop iteration first calls XAUTOCLAIM to
    reassign PEL (Pending Entries List) entries that have been idle for at
    least `claim_min_idle_ms` without an ACK to itself, and processes them
    first — if a consumer dies mid-processing (killed before it could ACK),
    the entry is not lost; another consumer (or the same one, restarted)
    picks it up.
  - Idempotency: a reclaimed entry may have already run through a handler
    on the original consumer, which then died before it could ACK — i.e.
    at-least-once delivery does not imply exactly-once processing. To guard
    against that, `SET NX EX` claims the `event_id` before invoking any
    handler; an event_id that was already claimed is ACKed without calling
    handlers again.
  - Ordering: with exactly one active consumer per group, entries are
    processed in the order they were appended. Once multiple consumers
    (multiple instances) join the same group, Redis round-robins entries
    across them, so global ordering across the whole topic is no longer
    guaranteed — in exchange for the much stronger guarantee that the same
    entry is never delivered to two consumers at once (zero duplicates).
    Same trade-off as Kafka's partition/consumer model.
  - Backpressure: unlike `asyncio.Queue`, a Redis Stream is effectively
    unbounded, so in_process's queue-saturation policy (§8.6) does not
    apply to this backend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Any, cast
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import ResponseError

from src.core.event_bus.bus import EventBus, EventHandler
from src.core.event_bus.envelope import EventEnvelope, wrap
from src.core.event_bus.policy import HandlerCriticality
from src.core.event_bus.redis_streams_codec import deserialize_envelope, serialize_envelope
from src.core.event_bus.redis_streams_dispatch import (
    HANDLER_ESCALATED_TOPIC,
    AuditSink,
    HandlerDispatcher,
)

logger = logging.getLogger(__name__)

__all__ = ["RedisStreamsEventBus", "HANDLER_ESCALATED_TOPIC"]

DEFAULT_CONSUMER_GROUP = "aios-event-bus"
DEFAULT_BLOCK_MS = 1000
DEFAULT_BATCH_SIZE = 10
DEFAULT_CLAIM_MIN_IDLE_MS = 30_000
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 86_400

STREAM_KEY_PREFIX = "aios:event_bus:stream:"
IDEMPOTENCY_KEY_PREFIX = "aios:event_bus:idemp:"


def _default_consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class RedisStreamsEventBus(EventBus):
    """M2-8 Phase2 implementation. One Redis Stream per topic + a shared
    consumer group.

    Passing `redis_client` means this instance does not own that client's
    lifecycle (used in tests where several bus instances share a
    connection, or the caller manages the pool itself). Passing only
    `redis_url` makes this instance create and later close its own client
    in `stop()`.
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client: redis.Redis | None = None,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        consumer_name: str | None = None,
        block_ms: int = DEFAULT_BLOCK_MS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        claim_min_idle_ms: int = DEFAULT_CLAIM_MIN_IDLE_MS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_initial_delay_seconds: float = DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
        idempotency_ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if redis_client is not None:
            self._owns_client = False
            self._redis: redis.Redis = redis_client
        elif redis_url is not None:
            self._owns_client = True
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("RedisStreamsEventBus: one of redis_url or redis_client is required")
        self._group = consumer_group
        self._consumer_name = consumer_name or _default_consumer_name()
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._claim_min_idle_ms = claim_min_idle_ms
        self._idempotency_ttl_seconds = idempotency_ttl_seconds
        self._dispatcher = HandlerDispatcher(
            escalate=self.publish,
            max_retries=max_retries,
            retry_initial_delay_seconds=retry_initial_delay_seconds,
            audit_sink=audit_sink,
        )

        self._subscribers: dict[str, list[tuple[EventHandler, HandlerCriticality]]] = {}
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}
        self._groups_ready: set[str] = set()
        self._running = False

    @staticmethod
    def stream_key(topic: str) -> str:
        return f"{STREAM_KEY_PREFIX}{topic}"

    def subscribe(
        self, topic: str, handler: EventHandler, *, criticality: HandlerCriticality
    ) -> None:
        self._subscribers.setdefault(topic, []).append((handler, criticality))
        if self._running:
            self._ensure_worker(topic)

    async def publish(self, topic: str, payload: Any) -> None:
        envelope = wrap(topic, payload)
        body = serialize_envelope(envelope)
        await self._redis.xadd(self.stream_key(topic), {"envelope": body})

    async def start(self) -> None:
        self._running = True
        for topic in self._subscribers:
            await self._ensure_group(topic)
            self._ensure_worker(topic)

    async def stop(self) -> None:
        """Graceful shutdown — each worker returns once its current batch is
        done. Entries left un-ACKed stay in the PEL; the next `start()`
        (same process or a different instance) reclaims them via
        XAUTOCLAIM — nothing is lost."""
        self._running = False
        tasks = list(self._worker_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_tasks.clear()
        if self._owns_client:
            await self._redis.aclose()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _ensure_group(self, topic: str) -> None:
        if topic in self._groups_ready:
            return
        try:
            await self._redis.xgroup_create(
                self.stream_key(topic), self._group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups_ready.add(topic)

    def _ensure_worker(self, topic: str) -> None:
        if topic in self._worker_tasks and not self._worker_tasks[topic].done():
            return
        self._worker_tasks[topic] = asyncio.create_task(self._worker_loop(topic))

    async def _worker_loop(self, topic: str) -> None:
        stream = self.stream_key(topic)
        await self._ensure_group(topic)
        while True:
            try:
                await self._reclaim_stale(topic, stream)
                resp = await self._redis.xreadgroup(
                    self._group,
                    self._consumer_name,
                    {stream: ">"},
                    count=self._batch_size,
                    block=self._block_ms,
                )
            except Exception:  # noqa: BLE001 — if a Redis connection error killed
                # this worker task, the topic would silently stop being consumed
                # from that point on (same defense-in-depth principle as
                # in_process's _worker_loop). Back off briefly and keep retrying.
                logger.exception("[%s] Redis Streams read failed — retrying", topic)
                if not self._running:
                    return
                await asyncio.sleep(1.0)
                continue
            if resp:
                # With decode_responses=True and a single STREAMS key, RESP2
                # always replies as `[[stream_name, entries]]`. redis-py's type
                # stub is a wider union that also allows the RESP3 dict shape,
                # so unpacking directly makes mypy flag a "string unpacking"
                # error — cast to the shape observed by hand (manual xreadgroup
                # call against a real server).
                resp_pairs = cast("list[tuple[str, list[tuple[str, dict[str, str]]]]]", resp)
                for _stream_name, entries in resp_pairs:
                    await self._process_entries(topic, stream, entries)
            if not self._running:
                return

    async def _reclaim_stale(self, topic: str, stream: str) -> None:
        """PLT-06 crash recovery — reassign entries a previous consumer left
        un-ACKed (idle >= claim_min_idle_ms) to this consumer and process
        them first."""
        cursor = "0-0"
        while True:
            cursor, claimed, _deleted = await self._redis.xautoclaim(
                stream,
                self._group,
                self._consumer_name,
                min_idle_time=self._claim_min_idle_ms,
                start_id=cursor,
                count=self._batch_size,
            )
            if claimed:
                await self._process_entries(topic, stream, claimed)
            if cursor in ("0-0", 0, b"0-0"):
                break

    async def _process_entries(
        self, topic: str, stream: str, entries: list[tuple[str, dict[str, str]]]
    ) -> None:
        for entry_id, fields in entries:
            envelope = self._parse_envelope(topic, fields)
            if envelope is not None:
                should_process = await self._claim_idempotency(topic, envelope.event_id)
                if should_process:
                    for handler, criticality in list(self._subscribers.get(topic, [])):
                        await self._dispatcher.dispatch(topic, handler, criticality, envelope)
                else:
                    logger.info(
                        "[%s] duplicate delivery (event_id=%s) — idempotency guard "
                        "skipped re-invoking handlers",
                        topic,
                        envelope.event_id,
                    )
            await self._redis.xack(stream, self._group, entry_id)

    def _parse_envelope(self, topic: str, fields: dict[str, str]) -> EventEnvelope | None:
        raw = fields.get("envelope")
        if raw is None:
            logger.error("[%s] entry missing the envelope field — skipping and ACKing", topic)
            return None
        try:
            return deserialize_envelope(raw)
        except Exception:  # noqa: BLE001 — a poison entry must not stall the worker
            logger.exception(
                "[%s] failed to deserialize envelope — skipping and ACKing this entry", topic
            )
            return None

    async def _claim_idempotency(self, topic: str, event_id: UUID) -> bool:
        """`True` means this consumer is processing this event_id for the
        first time (handlers should run). `False` means it was already
        processed by a previous delivery (do not call handlers again)."""
        key = f"{IDEMPOTENCY_KEY_PREFIX}{topic}:{event_id}"
        claimed = await self._redis.set(key, "1", nx=True, ex=self._idempotency_ttl_seconds)
        return bool(claimed)
