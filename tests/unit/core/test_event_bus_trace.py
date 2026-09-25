"""PLT-06 — Event Bus 봉투(trace 컨텍스트 전파) 단위 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-06
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.core.event_bus.envelope import EventEnvelope, unwrap
from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality
from src.core.observability.context import bind, current

# ADR-2026-09-09-C §행 "축별 성능 예산" — Event Bus 전용 항목은 없지만, 가장
# 근접한 비교축(사전거래 게이트 p99 5ms)을 publish() 고정비 예산으로 차용한다.
PUBLISH_P95_BUDGET_MS = 5.0


def _envelope_kwargs(**overrides):
    kwargs = {
        "event_id": uuid.uuid4(),
        "topic": "t",
        "trace_id": uuid.uuid4(),
        "tenant_id": None,
        "actor_subject_id": "system",
        "occurred_at": datetime.now(timezone.utc),
        "payload": {},
    }
    kwargs.update(overrides)
    return kwargs


def _fast_bus() -> InProcessEventBus:
    return InProcessEventBus(
        max_queue_depth=10,
        backpressure_sustained_seconds=0.05,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
    )


class _UnboundEventBus(InProcessEventBus):
    """PLT-06 수정(봉투 바인딩)을 의도적으로 생략한 워커 — 게이트 적색 재현
    전용. 프로덕션 코드를 건드리지 않고, `_dispatch`의 `bind()` 호출만
    제거한 회귀 시나리오를 별도 서브클래스로 재현한다."""

    async def _dispatch(self, topic, handler, criticality, envelope, payload):
        try:
            await handler(payload)
        except Exception:  # noqa: BLE001, S110 — 재현용, 원본 에러 정책은 검증 대상 아님
            pass


async def test_handler_sees_publish_time_trace_context():
    """publish() 호출 시점에 바인딩된 trace_id/tenant_id를, 별도 워커
    코루틴에서 실행되는 핸들러가 current()로 그대로 관측해야 한다."""
    bus = _fast_bus()
    seen: list[uuid.UUID] = []

    async def handler(payload):
        seen.append(current().trace_id)

    bus.subscribe("market.ticker.updated", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    tenant_id = uuid.uuid4()
    with bind(tenant_id=tenant_id) as ctx:
        await bus.publish("market.ticker.updated", {"symbol": "BTC/USDT"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.05)
    await bus.stop()

    assert seen == [published_trace_id]


async def test_context_does_not_leak_when_published_without_binding():
    """컨텍스트 없이 발행해도 핸들러 실행 전후 프로세스 전역 컨텍스트가
    오염되지 않는다 — 핸들러 안에서 본 trace_id는 발행측 fallback 값과
    일치하되, 핸들러 종료 후에는 그 값이 바깥으로 새어 나오지 않는다."""
    bus = _fast_bus()
    seen: list[uuid.UUID] = []

    async def handler(payload):
        seen.append(current().trace_id)

    bus.subscribe("market.ticker.updated", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    before = current().trace_id
    await bus.publish("market.ticker.updated", {"symbol": "BTC/USDT"})
    await asyncio.sleep(0.05)
    await bus.stop()
    after = current().trace_id

    assert len(seen) == 1
    assert seen[0] not in (before, after)
    assert before != after  # 바인딩 전 current()는 매 호출마다 새 임시값


async def test_context_restored_after_handler_raises():
    """핸들러가 예외를 던져도(SAFE 정책 경로) 바인딩된 컨텍스트는 해당
    handler 실행 구간에만 유효하고, 워커 루프로 복귀하면 원복된다."""
    bus = _fast_bus()

    async def failing_handler(payload):
        raise RuntimeError("boom")

    bus.subscribe("order.status.changed", failing_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    with bind(tenant_id=uuid.uuid4()) as ctx:
        await bus.publish("order.status.changed", {"order_id": "1"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.05)
    await bus.stop()

    # 발행측 바인딩 블록을 벗어난 뒤이므로, 핸들러의 예외 여부와 무관하게
    # 현재 프로세스 컨텍스트는 이미 발행 시점 trace_id와 달라야 한다.
    assert current().trace_id != published_trace_id


async def test_context_restored_after_critical_handler_exhausts_retries():
    """CRITICAL 핸들러가 재시도까지 전부 실패해도(escalate_and_retry) 컨텍스트
    누수 없이 원복된다."""
    bus = _fast_bus()

    async def always_fails(payload):
        raise RuntimeError("persistent failure")

    bus.subscribe("order.status.changed", always_fails, criticality=HandlerCriticality.CRITICAL)
    await bus.start()

    with bind(tenant_id=uuid.uuid4()) as ctx:
        await bus.publish("order.status.changed", {"order_id": "2"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.2)
    await bus.stop()

    assert current().trace_id != published_trace_id


# ----------------------------------------------------------------------
# negative — EventEnvelope 모델 검증 (malformed / 불변성 위반)
# ----------------------------------------------------------------------


def test_envelope_is_frozen_rejects_mutation():
    """negative — 발행된 봉투는 `frozen=True` 계약이므로 필드 재할당이
    거부되어야 한다(§PLT-06 "payload 포함 불변 스냅샷")."""
    envelope = EventEnvelope(**_envelope_kwargs())

    with pytest.raises(ValidationError):
        envelope.trace_id = uuid.uuid4()


def test_envelope_rejects_malformed_trace_id():
    """negative — trace_id가 UUID로 파싱 불가능한 문자열이면 생성 자체가
    거부되어야 한다(외부/역직렬화 입력이 오염된 경우를 가정)."""
    with pytest.raises(ValidationError):
        EventEnvelope(**_envelope_kwargs(trace_id="not-a-uuid"))


def test_envelope_rejects_actor_subject_id_outside_union():
    """negative — actor_subject_id는 `UUID | Literal["system"]`만 허용한다.
    둘 다 아닌 임의 문자열은 거부되어야 한다(권한 우회 문자열 주입 방지)."""
    with pytest.raises(ValidationError):
        EventEnvelope(**_envelope_kwargs(actor_subject_id="not-system-and-not-a-uuid"))


def test_unwrap_passthrough_for_non_envelope_payload():
    """경계 — 봉투가 아닌 값이 큐에 들어와도(전환기 호환 경로) `unwrap()`은
    예외 없이 `(None, 원본값)`을 그대로 돌려준다."""
    raw = {"symbol": "BTC/USDT"}

    envelope, payload = unwrap(raw)

    assert envelope is None
    assert payload is raw


# ----------------------------------------------------------------------
# 실패 주입 — audit_sink 자체가 장애를 일으키는 회귀 재현
# ----------------------------------------------------------------------


async def test_audit_sink_failure_does_not_kill_worker_loop():
    """실패 주입 — 실 audit_log 연동이 장애를 일으키는 상황(레드팀 #15)을
    audit_sink 콜백 예외로 시뮬레이션한다. `_handle_safe_error`의 보호용
    try/except(in_process.py:183-194)가 실제로 워커 태스크를 살려서
    동일 토픽의 다음 이벤트를 계속 처리하는지 증명한다 — 존재만으로
    통과를 가정하지 않는다."""

    async def failing_audit_sink(record):
        raise RuntimeError("audit backend unreachable")

    bus = InProcessEventBus(
        max_queue_depth=10,
        backpressure_sustained_seconds=0.05,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
        audit_sink=failing_audit_sink,
    )
    processed: list[str] = []

    async def handler(payload):
        if payload["id"] == "1":
            raise RuntimeError("boom")
        processed.append(payload["id"])

    bus.subscribe("order.status.changed", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    await bus.publish("order.status.changed", {"id": "1"})
    await bus.publish("order.status.changed", {"id": "2"})
    await asyncio.sleep(0.1)
    await bus.stop()

    assert processed == ["2"]


# ----------------------------------------------------------------------
# 성능 단언 — publish() 고정비 p95 (ADR-2026-09-09-C 축별 예산 차용)
# ----------------------------------------------------------------------


@pytest.mark.perf
async def test_publish_dispatch_latency_p95_within_budget():
    """수치 성능 단언 — publish()(봉투 wrap + 큐 적재) 1회 호출의 p95
    지연시간이 예산(5ms)을 넘지 않아야 한다. 실측 기준선은 ~0.01ms
    수준(500배 이상 여유) — 이 단언은 in-process 경로가 회귀로 인해
    수 ms 이상 느려지는 것을 잡기 위한 것이지, 현재 실측치에 딱 맞춘
    타이트한 문턱이 아니다."""
    bus = InProcessEventBus(
        max_queue_depth=2000,
        backpressure_sustained_seconds=60.0,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
    )

    async def handler(payload):
        pass

    bus.subscribe("perf.topic", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    n = 500
    latencies: list[float] = []
    for i in range(n):
        start = time.perf_counter()
        await bus.publish("perf.topic", {"i": i})
        latencies.append(time.perf_counter() - start)

    await asyncio.sleep(0.1)
    await bus.stop()

    latencies.sort()
    p95_ms = latencies[int(n * 0.95)] * 1000
    assert p95_ms < PUBLISH_P95_BUDGET_MS, (
        f"publish() p95 지연시간 {p95_ms:.4f}ms가 예산 {PUBLISH_P95_BUDGET_MS}ms 초과"
    )


# ----------------------------------------------------------------------
# 게이트 적색 재현 — PLT-06 봉투 바인딩을 제거하면 회귀 테스트가 실패해야 함
# ----------------------------------------------------------------------


async def test_gate_red_reproduction_without_envelope_binding():
    """게이트 적색 재현 — `_dispatch`의 `bind(envelope...)` 호출(PLT-06 수정
    그 자체)이 빠지면, `test_handler_sees_publish_time_trace_context`가
    검증하는 "핸들러가 발행 시점 trace_id를 본다"는 단언이 실제로 실패
    (적색)한다는 것을 `_UnboundEventBus`로 직접 재현한다. 이 테스트가 통과
    한다는 것은 기존 양성 테스트들이 공허하게 통과하는 게 아니라 실제
    회귀를 잡아낸다는 증거다."""
    bus = _UnboundEventBus(
        max_queue_depth=10,
        backpressure_sustained_seconds=0.05,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
    )
    seen: list[uuid.UUID] = []

    async def handler(payload):
        seen.append(current().trace_id)

    bus.subscribe("market.ticker.updated", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    with bind(tenant_id=uuid.uuid4()) as ctx:
        await bus.publish("market.ticker.updated", {"symbol": "BTC/USDT"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.05)
    await bus.stop()

    assert len(seen) == 1
    # bind()가 빠진 워커는 publish 시점 컨텍스트를 물려받지 못하므로, 핸들러가
    # 관측한 trace_id는 발행 시점 값과 달라야 한다 — PLT-06 수정이 없었다면
    # 이 저장소의 회귀 테스트가 정확히 이렇게 적색이 됐을 것이다.
    assert seen[0] != published_trace_id
