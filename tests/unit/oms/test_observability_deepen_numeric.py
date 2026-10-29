"""L4-27 DEEPEN(task-2801) — 관측성 배선 D3 보강(2/2): 수치 latency/throughput,
multi-instance, adversarial/replay.

`test_observability_deepen.py`(failure-injection 절)에서 파일 크기 래칫
(ADR-2026-09-10-C §7) 때문에 책임 단위로 분리했다. 공용 fake/헬퍼는 원본
모듈에서 가져온다 — 설계 배경은 원본 docstring 참조."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

import src.services.oms.application.outbox_dispatcher as outbox_dispatcher_module
import src.services.oms.application.unknown_resolver as unknown_resolver_module
from src.core.observability.metric_names import (
    OMS_INBOX_DUPLICATE_COUNT_TOTAL,
    OMS_OUTBOX_DISPATCH_DURATION_SECONDS,
    OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS,
)
from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.unknown_resolver import resolve_unknown
from tests.support.oms_outbox_fakes import (
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    enqueue,
    make_dispatcher,
    make_order_view,
)
from tests.unit.oms.test_observability_deepen import (
    _AlwaysDuplicateInboxRepo,
    _FakePool,
    _found_order,
    _FoundAdapter,
    _provider_event,
    _SingleUnknownOrderRepo,
    _SpyMetrics,
    _StepMonotonic,
    _unknown_order_view,
    _UnknownFakePool,
    _UnusedRiskGateRepo,
)

# ==================================================================================
# 2) 수치 latency/throughput — 결정론적 가짜 시계로 정확한 값을 단언한다
# ==================================================================================


async def test_outbox_duration_metric_is_deterministic_and_bounds_throughput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = 0.25
    fake_time = _StepMonotonic(step)
    monkeypatch.setattr(outbox_dispatcher_module, "time", fake_time)
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    n = 6
    for _ in range(n):
        view = orders.add(make_order_view())
        await enqueue(outbox, view)
    adapter = ScriptedAdapter()
    spy = _SpyMetrics()
    dispatcher = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, clock=clock, metrics=spy
    )

    report = await dispatcher.dispatch_once(limit=n)

    assert report.acknowledged == n
    durations = [
        v for name, v, _ in spy.observations if name == OMS_OUTBOX_DISPATCH_DURATION_SECONDS
    ]
    assert durations == [step] * n  # 행마다 정확히 `step`초(가짜 monotonic 2회 호출)
    throughput = n / sum(durations)
    assert throughput == pytest.approx(1.0 / step)  # 결정론적 처리량(초당 4행)


async def test_unknown_resolution_duration_metric_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = 0.25
    fake_time = _StepMonotonic(step)
    monkeypatch.setattr(unknown_resolver_module, "time", fake_time)
    view = _unknown_order_view()
    repo = _SingleUnknownOrderRepo(view)
    adapter = _FoundAdapter(_found_order())
    spy = _SpyMetrics()
    risk_gate_repo: RiskGateRepository = _UnusedRiskGateRepo()

    result = await resolve_unknown(
        view.order_id, adapter=adapter, pool=_UnknownFakePool(),
        risk_gate_repo=risk_gate_repo,
        clock=lambda: datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        order_repo=repo, metrics=spy,
    )

    assert result.status is OrderStatus.ACKNOWLEDGED
    durations = [
        v for name, v, _ in spy.observations
        if name == OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS
    ]
    assert durations == [step]


# ==================================================================================
# 3) multi-instance — 두 워커가 동시에 돌아도 행당 정확히 1회만 기록된다
# ==================================================================================


def _yielding_ack_adapter(times: int = 3) -> ScriptedAdapter:
    """`test_concurrent_dispatchers.py`(task-2759)와 동일한 인터리빙 기법 —
    어댑터 훅 안에서 여러 번 이벤트 루프를 양보해 두 워커가 실제로 겹쳐
    돌게 만든다."""

    async def hook(order: Order) -> Order:
        for _ in range(times):
            await asyncio.sleep(0)
        return order.model_copy(
            update={
                "exchange_order_id": f"ex-{order.client_order_id}",
                "status": OrderStatus.SUBMITTED,
            }
        )

    return ScriptedAdapter(on_place=hook)


async def test_outbox_duration_metric_recorded_exactly_once_per_row_across_workers() -> None:
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    n = 10
    for _ in range(n):
        view = orders.add(make_order_view())
        await enqueue(outbox, view)
    adapter = _yielding_ack_adapter()
    spy = _SpyMetrics()  # 두 워커가 같은 spy를 공유한다 — 실제 Prometheus 레지스트리와
    # 동일하게 전역 집계이므로, 이 spy 하나로 "중복도 누락도 없다"를 직접 잰다.
    d1 = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, worker_id="w1", clock=clock, metrics=spy
    )
    d2 = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, worker_id="w2", clock=clock, metrics=spy
    )

    half = n // 2  # 한 워커가 배치 전체를 선점하지 못하게 나눈다 — 둘 다 실제로 겹쳐 돈다
    reports = await asyncio.gather(d1.dispatch_once(limit=half), d2.dispatch_once(limit=half))

    assert sum(r.claimed for r in reports) == n
    assert sum(r.acknowledged for r in reports) == n
    assert sum(r.conflicts + r.errors for r in reports) == 0
    durations = [
        v for name, v, _ in spy.observations if name == OMS_OUTBOX_DISPATCH_DURATION_SECONDS
    ]
    assert len(durations) == n  # 행당 정확히 1회 — 두 워커 합산도 중복·누락 없음
    assert all(v >= 0.0 for v in durations)
    assert len({r.worker_id for r in outbox.rows.values()}) > 1  # 실제로 두 워커가 나눠 처리


# ==================================================================================
# 4) adversarial/replay — 대량 재생과 검증되지 않은 라벨에도 집계가 정확하다
# ==================================================================================


async def test_inbox_duplicate_counter_matches_exact_replay_storm_count() -> None:
    """같은 이벤트를 500회 동시 `ingest()`로 재생한다(§9 L4-15 "1000회 동시
    중복 전달"과 동일 취지, 여기서는 계측 카운터의 정확도만 본다) — 이벤트
    루프가 단일 스레드라 실제 경쟁은 없지만, 재생 규모에서도 계측 호출이
    누락·중복 없이 정확히 재생 횟수만큼 기록됨을 확인한다."""
    spy = _SpyMetrics()
    processor = InboxProcessor(_FakePool(), inbox_repo=_AlwaysDuplicateInboxRepo(), metrics=spy)
    n = 500
    ev = _provider_event()

    results = await asyncio.gather(*(processor.ingest(ev) for _ in range(n)))

    assert results == [False] * n
    assert len(spy.counters) == n
    assert all(
        c == (OMS_INBOX_DUPLICATE_COUNT_TOTAL, {"venue": "bitget", "source": "WS"})
        for c in spy.counters
    )


async def test_inbox_duplicate_counter_records_adversarial_venue_label_without_crashing() -> None:
    """`ProviderOrderEvent.venue`는 평범한 `str`이라 길이·문자 제한이 없다 —
    이 테스트는 "안전하다"는 주장이 아니라 현재 동작(공격적으로 긴/특수문자
    섞인 값도 그대로 라벨로 통과한다)을 기록한다. 계측 경로가 이런 입력에
    크래시하지 않는다는 것만 이 리프의 관심사다."""
    spy = _SpyMetrics()
    processor = InboxProcessor(_FakePool(), inbox_repo=_AlwaysDuplicateInboxRepo(), metrics=spy)
    adversarial_venue = "bitget'; DROP TABLE orders;--" + ("X" * 2000)
    ev = _provider_event(venue=adversarial_venue)

    result = await processor.ingest(ev)

    assert result is False
    assert spy.counters == [
        (OMS_INBOX_DUPLICATE_COUNT_TOTAL, {"venue": adversarial_venue, "source": "WS"})
    ]
