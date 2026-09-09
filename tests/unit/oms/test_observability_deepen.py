"""L4-27 DEEPEN(task-2801) — 관측성 배선 D3 보강: failure-injection/수치 perf/
multi-instance/adversarial-replay.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md)가 원 리프(task-2322, 09a985e6 —
`test_observability_wiring.py`/`test_metrics_names.py`)를 D1로 판정했다: negative/
reject-format 단언(7개 리터럴)과 FakeMetrics 배선 증명, redaction negative test는
있었지만 (a) failure-injection(crash/DB/network), (b) 수치 latency/throughput
단언, (c) multi-instance/adversarial/replay 증명이 없었다. 이 파일이 세 계측
지점(`OMS_OUTBOX_DISPATCH_DURATION_SECONDS`/`OMS_INBOX_DUPLICATE_COUNT_TOTAL`/
`OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS`)에 대해 그 세 가지만 보강한다 —
원본 테스트 파일과 계측 대상 소스는 손대지 않는다(`test_unknown_resolver_
failure_injection.py`/task-2769와 동일 관례).

failure-injection: outbox/unknown_resolver 둘 다 `self._metrics.observe(...)`
호출이 쓰기 성공 *이후*에만 실행된다(각각 `_send_submit`/`resolve_unknown`
본문 순서). 쓰기가 DB 연결 유실로 크래시하면 그 사실이 거짓 성공 latency로
기록되면 안 된다 — 아래 테스트들이 관측치 0건을 확인한다. inbox는 중복
판정(`insert_if_absent`) 자체가 크래시해도 "중복"으로 잘못 집계되지 않음을
확인한다.

수치 latency/throughput: `time.monotonic`을 전역 `time` 모듈이 아니라 각
계측 모듈의 네임스페이스(`outbox_dispatcher.time`/`unknown_resolver.time`)만
치환해 결정론적으로 만든다 — asyncio 내부 스케줄링에 쓰이는 실제 `time`
모듈은 건드리지 않는다. 스텝 값 0.25는 이진 부동소수점 정확값이라 반복
곱셈/뺄셈에도 반올림 오차가 없다(`task-2769`가 지적한 절대 ms 상수의 CI
환경 편차 문제와 무관 — 이건 실 벽시계가 아니라 가짜 시계다).

multi-instance: `test_concurrent_dispatchers.py`(task-2759)의 `_yielding`
기법(어댑터 훅 안 `await asyncio.sleep(0)`으로 진짜 인터리빙을 만든다)을
재사용해, 두 워커가 동시에 디스패치해도 지연 메트릭이 행당 정확히 1회만
기록됨(중복도 누락도 없음)을 증명한다.

adversarial/replay: 같은 중복 이벤트를 대량 동시 `ingest()`로 재생해도
`OMS_INBOX_DUPLICATE_COUNT_TOTAL`이 실제 재생 횟수와 정확히 일치하는지,
그리고 검증되지 않은 `venue` 문자열(라벨 카디널리티 폭발 공격 벡터 —
`ProviderOrderEvent.venue`는 평범한 `str`이라 길이·문자 제한이 없다)이
계측 경로를 크래시시키지 않는지 확인한다. 후자는 "안전하다"는 주장이 아니라
현재 동작(라벨이 그대로 통과한다)을 기록하는 관측 테스트다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

import src.services.oms.application.outbox_dispatcher as outbox_dispatcher_module
import src.services.oms.application.unknown_resolver as unknown_resolver_module
from src.core.observability.metric_names import (
    OMS_INBOX_DUPLICATE_COUNT_TOTAL,
    OMS_OUTBOX_DISPATCH_DURATION_SECONDS,
    OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS,
)
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.foundation.risk_gate.domain.models import (
    FenceSnapshot,
    RiskEvaluation,
    SafetyControl,
    SafetyScope,
)
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.unknown_resolver import resolve_unknown
from src.services.oms.contracts.v1_events import ProviderOrderEvent
from src.services.oms.contracts.v1_views import OrderView
from tests.support.oms_outbox_fakes import (
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    enqueue,
    make_dispatcher,
    make_order_view,
)


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


class _StepMonotonic:
    """`time.monotonic`을 결정론적으로 흉내 낸다 — 호출 n번째에 `n*step`을 돌려준다.

    `step`이 이진 정확값(0.25 = 1/4)이면 어떤 두 호출 사이의 차도 반올림 오차
    없이 정확히 `step`이다."""

    def __init__(self, step: float) -> None:
        self._step = step
        self.calls = 0

    def monotonic(self) -> float:
        t = self.calls * self._step
        self.calls += 1
        return t


# ==================================================================================
# 1) failure-injection — 쓰기 크래시 시 거짓 성공 metric이 기록되지 않는다
# ==================================================================================


class _CrashOnceOrderRepo(InMemoryOrderRepo):
    """`crash_on` 상태로의 전이 1회에 한해 DB 연결 유실을 흉내 낸다(그 뒤로는
    정상 동작 — 이 테스트는 1회 크래시의 관측 효과만 본다)."""

    def __init__(self, *, crash_on: OrderStatus) -> None:
        super().__init__()
        self._crash_on = crash_on
        self.crash_count = 0

    async def transition(self, conn: Any, **kwargs: Any) -> OrderView:
        if kwargs["new_status"] is self._crash_on and self.crash_count == 0:
            self.crash_count += 1
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "simulated DB connection loss mid ACK transition"
            )
        return await super().transition(conn, **kwargs)


async def test_outbox_duration_metric_not_observed_when_ack_write_crashes() -> None:
    """`_send_submit`의 `self._metrics.observe(...)`는 `_finalize_submit` 반환
    *이후*에만 실행된다 — ACK 전이 자체가 DB 연결 유실로 크래시하면(어댑터
    호출은 이미 성공한 뒤) 그 행의 latency가 거짓으로 기록되면 안 된다."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = _CrashOnceOrderRepo(crash_on=OrderStatus.ACKNOWLEDGED)
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter()  # 거래소 호출은 성공한다 — 크래시는 그 이후 쓰기다
    spy = _SpyMetrics()
    dispatcher = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, clock=clock, metrics=spy
    )

    report = await dispatcher.dispatch_once()

    assert report.errors == 1
    assert report.acknowledged == 0
    assert spy.observations == [], (
        "쓰기가 크래시했는데도 지연 메트릭이 기록됐다 — 거짓 성공 latency."
    )
    assert outbox.rows[row_id].state == "SENDING"  # 롤백됨 — lease 만료 후 복구 대상
    assert orders.orders[view.order_id].status is OrderStatus.SUBMITTED  # ACK로 전진 못함


class _AlwaysDuplicateInboxRepo:
    async def insert_if_absent(self, conn: object, ev: ProviderOrderEvent) -> bool:
        return False

    async def claim_unprocessed(self, conn: object, *, limit: int) -> list[Any]:
        raise NotImplementedError

    async def mark_processed(self, conn: object, id: UUID, *, expected_state: str = "NEW") -> None:
        raise NotImplementedError


class _CrashOnInsertInboxRepo:
    """duplicate 판정(`insert_if_absent`) 자체가 DB 연결 유실로 크래시하는
    경로 — 이 크래시가 "중복"으로 잘못 집계되면 안 된다."""

    def __init__(self) -> None:
        self.calls = 0

    async def insert_if_absent(self, conn: object, ev: ProviderOrderEvent) -> bool:
        self.calls += 1
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated inbox insert DB crash")

    async def claim_unprocessed(self, conn: object, *, limit: int) -> list[Any]:
        raise NotImplementedError

    async def mark_processed(self, conn: object, id: UUID, *, expected_state: str = "NEW") -> None:
        raise NotImplementedError


class _FakeTxCtx:
    async def __aenter__(self) -> _FakeTxCtx:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConn:
    def transaction(self) -> _FakeTxCtx:
        return _FakeTxCtx()


class _FakeAcquireCtx:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx()


def _provider_event(**overrides: Any) -> ProviderOrderEvent:
    fields: dict[str, Any] = dict(
        provider_event_id="ws-1",
        venue="bitget",
        venue_symbol="BTCUSDT",
        exchange_order_id="ex-1",
        client_order_id=None,
        venue_status="filled",
        filled_quantity=Decimal("1"),
        average_price=None,
        last_fill=None,
        venue_ts=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc),
        source="WS",
        raw_hash="deadbeef",
    )
    fields.update(overrides)
    return ProviderOrderEvent(**fields)


async def test_inbox_duplicate_counter_not_recorded_when_insert_check_crashes() -> None:
    spy = _SpyMetrics()
    processor = InboxProcessor(_FakePool(), inbox_repo=_CrashOnInsertInboxRepo(), metrics=spy)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await processor.ingest(_provider_event())

    assert spy.counters == []  # 삽입 자체가 실패 — "중복"으로 잘못 집계되지 않는다


class _UnknownFakeTx:
    async def start(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _UnknownFakeConn:
    def transaction(self) -> _UnknownFakeTx:
        return _UnknownFakeTx()


class _UnknownConnCtx:
    async def __aenter__(self) -> _UnknownFakeConn:
        return _UnknownFakeConn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _UnknownFakePool:
    def acquire(self) -> _UnknownConnCtx:
        return _UnknownConnCtx()


class _CrashOnceUnknownOrderRepo:
    """RESOLVED_AS 확정 전이(`repo.transition`) 1회에 한해 DB 연결 유실을
    흉내 낸다. `get_for_update`는 크래시 없이 항상 캐시된 UNKNOWN 뷰를 준다."""

    def __init__(self, order: OrderView) -> None:
        self.order = order
        self.crash_count = 0

    async def get_for_update(self, conn: object, order_id: UUID) -> OrderView:
        return self.order

    async def find_by_scope_hash(self, conn: object, scope_hash: str) -> OrderView | None:
        raise NotImplementedError

    async def transition(
        self, conn: object, *, order_id: UUID, expected_status: OrderStatus,
        expected_version: int, new_status: OrderStatus, patch: dict[str, Any], event: Any,
    ) -> OrderView:
        if self.crash_count == 0:
            self.crash_count += 1
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "simulated DB connection loss mid RESOLVED_AS transition"
            )
        self.order = self.order.model_copy(
            update={**patch, "status": new_status, "version": self.order.version + 1}
        )
        return self.order


class _SingleUnknownOrderRepo:
    """크래시 없는 버전 — 결정론적 latency 테스트 전용(정상 RESOLVED_AS 1회)."""

    def __init__(self, order: OrderView) -> None:
        self.order = order

    async def get_for_update(self, conn: object, order_id: UUID) -> OrderView:
        return self.order

    async def find_by_scope_hash(self, conn: object, scope_hash: str) -> OrderView | None:
        raise NotImplementedError

    async def transition(
        self, conn: object, *, order_id: UUID, expected_status: OrderStatus,
        expected_version: int, new_status: OrderStatus, patch: dict[str, Any], event: Any,
    ) -> OrderView:
        self.order = self.order.model_copy(
            update={**patch, "status": new_status, "version": self.order.version + 1}
        )
        return self.order


class _FoundAdapter:
    def __init__(self, found: Order) -> None:
        self._found = found

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        return self._found


class _UnusedRiskGateRepo:
    """RESOLVED_AS 경로는 이 포트를 실제로 호출하지 않는다 — 호출되면 이
    테스트가 그 자리에서 실패해 가정이 깨졌음을 드러낸다."""

    async def list_active_controls(
        self, *, tenant_id: UUID, provider_code: str | None = None,
        include_all_providers: bool = False,
    ) -> tuple[SafetyControl, ...]:
        raise NotImplementedError

    async def insert_safety_control(
        self, *, scope: SafetyScope, scope_ref: str, reason: str, actor_subject_id: UUID,
    ) -> SafetyControl:
        raise NotImplementedError

    async def get_safety_control(self, control_id: UUID) -> SafetyControl | None:
        raise NotImplementedError

    async def deactivate_safety_control(self, control_id: UUID) -> SafetyControl:
        raise NotImplementedError

    async def insert_evaluation(self, evaluation: RiskEvaluation) -> RiskEvaluation:
        raise NotImplementedError

    async def get_cached_evaluation(
        self, tenant_id: UUID, fingerprint: str
    ) -> RiskEvaluation | None:
        raise NotImplementedError

    async def read_fences(
        self, pairs: tuple[tuple[SafetyScope, str], ...]
    ) -> FenceSnapshot:
        raise NotImplementedError

    async def invalidate_evaluations(self, *, tenant_id: UUID | None) -> None:
        raise NotImplementedError

    async def read_fence_and_controls(
        self, pairs: tuple[tuple[SafetyScope, str], ...]
    ) -> tuple[FenceSnapshot, tuple[SafetyControl, ...]]:
        raise NotImplementedError

    async def read_safety_state(
        self, *, provider_code: str, symbol: str
    ) -> tuple[str | None, str | None]:
        raise NotImplementedError


def _unknown_order_view() -> OrderView:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OrderView(
        order_id=uuid4(), tenant_id=uuid4(), execution_id=1, client_order_id=f"c-{uuid4().hex}",
        exchange_order_id=None, symbol="BTC/USDT", venue_symbol="BTCUSDT", exchange="bitget",
        side=OrderSide.BUY, order_type=OrderType.MARKET, time_in_force="GTC",
        quantity=Decimal("1"), price=None, status=OrderStatus.UNKNOWN,
        filled_quantity=Decimal("0"), average_fill_price=None, fee_total=None, fee_currency=None,
        version=3, parent_order_id=None, algo_run_id=None, unknown_since=now,
        provider_order_date=None, created_at=now, updated_at=now,
    )


def _found_order() -> Order:
    return Order(
        client_order_id=f"c-{uuid4().hex}", strategy_id="s", strategy_version="1.0.0",
        execution_id=1, symbol="BTC/USDT", exchange="bitget", side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=Decimal("1"), asset_class=AssetClass.CRYPTO,
        status=OrderStatus.ACKNOWLEDGED, filled_quantity=Decimal("0"), exchange_order_id="ex-9",
    )


async def test_unknown_resolution_duration_metric_not_observed_when_transition_crashes() -> None:
    """`resolve_unknown`의 `_observe(m, start, "RESOLVED_AS")`는 `apply_resolved_as`
    반환 *이후*에만 실행된다 — 확정 전이가 DB 연결 유실로 크래시하면(어댑터
    역조회는 이미 성공한 뒤) 그 시도의 latency가 거짓으로 기록되면 안 된다."""
    view = _unknown_order_view()
    repo = _CrashOnceUnknownOrderRepo(view)
    adapter = _FoundAdapter(_found_order())
    spy = _SpyMetrics()
    risk_gate_repo: RiskGateRepository = _UnusedRiskGateRepo()

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await resolve_unknown(
            view.order_id, adapter=adapter, pool=_UnknownFakePool(),
            risk_gate_repo=risk_gate_repo,
            clock=lambda: datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
            order_repo=repo, metrics=spy,
        )

    assert spy.observations == [], (
        "확정 전이가 크래시했는데도 해소 latency가 기록됐다 — 거짓 성공 latency."
    )
    assert repo.order.status is OrderStatus.UNKNOWN  # 롤백 모델 — 상태 불변


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
        clock=lambda: datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
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
