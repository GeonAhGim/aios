"""L4-27 — task-3381(2328 DEEPEN, D3 안전축) 보강분: 계측 훅 실패 주입 +
적대적 재현 + 로그 필드 직렬화 성능 단언.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.2, §7.3, §9 L4-27
DoD(c)/(e). `test_observability_wiring.py`(09a985e6)는 세 계측 지점이 정확히
1회 호출됨만 증명했다 — 이 파일은 docs/audit/DEPTH_L4_BR.md 2328행이 지적한
나머지 D2/D3 결손을 메운다:

- 실패 주입(D2): `_RaisingMetrics`(모든 훅이 예외)로 세 계측 지점을 각각
  무너뜨려도 주문/이벤트/해소 경로의 실제 쓰기가 끝까지 커밋됨을 증명한다.
  `src/core/observability/metrics.py`의 `safe_observe`/`safe_counter`가 이
  경계를 만든다 — outbox_dispatcher/inbox_processor/unknown_resolver 세
  호출부가 전부 이 래퍼를 거치도록 이 리프에서 새로 배선했다.
- 적대적(D3, I-10 배선-증명 경계): 합성 예외가 아니라 `PrometheusMetrics`가
  실제로 던지는 실패 모드(라벨 키 재정의 `ValueError`,
  tests/unit/core/observability/test_metrics_port.py와 동일 근거)를 그대로
  재현해, 흡수는 OMS 호출부 경계에만 있고 어댑터 자체는 fail-closed로
  남는다는 두 계층의 책임 분리를 함께 확인한다.
- 성능 단언(D2): ADR-2026-09-09-C Decision 1 예산표에 로그 필드 직렬화 전용
  항목이 없어 가장 가까운 항목("사전거래 게이트 p99 5ms")을 자체 예산으로
  차용한다(task-3979/5239e273, task-3367 DEEPEN과 동일 차용 근거) —
  `_dispatch_log_extra` payload를 실제 프로덕션 포매터(`JSONLinesFormatter`)로
  직렬화한 p95가 그 예산 안에 들어와야 한다.

공유 픽스처(`_order_view`/`_found_order`/`_SingleOrderRepo`/`_FoundAdapter`/
`_UnusedRiskGateRepo`/`_UnknownFakePool`/`_AlwaysDuplicateInboxRepo`/
`_provider_event`)는 `test_observability_wiring.py`의 것을 그대로 재사용한다
— unknown_resolver 시나리오 하나에 필요한 스텁이 6개 클래스·80줄 안팎이라
새로 복제하면 두 파일이 서로 다른 계약으로 갈라질 위험이 더 크다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.core.logging.schema import JSONLinesFormatter
from src.core.observability.metrics import PrometheusMetrics, safe_counter, safe_observe
from src.data.models.trading import OrderStatus
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.outbox_dispatcher import _dispatch_log_extra
from src.services.oms.application.unknown_resolver import resolve_unknown
from src.services.oms.ports.repository import OutboxRow
from tests.support.oms_outbox_fakes import (
    FakePool,
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    enqueue,
    make_dispatcher,
    make_order_view,
)
from tests.unit.oms.test_observability_wiring import (
    _AlwaysDuplicateInboxRepo,
    _found_order,
    _FoundAdapter,
    _order_view,
    _provider_event,
    _SingleOrderRepo,
    _UnknownFakePool,
    _UnusedRiskGateRepo,
)


class _RaisingMetrics:
    """세 훅 전부가 예외를 던지는 계측 백엔드 — 실패 주입 전용."""

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        raise RuntimeError("metrics backend down")

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise RuntimeError("metrics backend down")

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise RuntimeError("metrics backend down")


# ---- 실패 주입 1/3: outbox_dispatcher — observe() 실패가 ACK 전이를 막지 않는다 ---


async def test_outbox_dispatcher_ack_transitions_survive_metrics_observe_failure() -> None:
    """`observe()`가 항상 예외를 던져도 SUBMIT ACK 주문 전이는 끝까지
    커밋되고 `dispatch_once()`도 이를 오류로 집계하지 않는다(`safe_observe`).
    2행을 함께 태워 한 행의 계측 실패가 다음 행 처리를 막지 않음도 같이
    증명한다(반복 실패에도 배치 전체가 살아남는지)."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    view1 = orders.add(make_order_view())
    view2 = orders.add(make_order_view())
    await enqueue(outbox, view1)
    await enqueue(outbox, view2)
    adapter = ScriptedAdapter()
    dispatcher = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, clock=clock, metrics=_RaisingMetrics()
    )

    report = await dispatcher.dispatch_once()

    assert report.acknowledged == 2
    assert report.errors == 0  # 관측 실패가 dispatch_once 오류 카운트로 새지 않는다
    assert orders.orders[view1.order_id].status is OrderStatus.ACKNOWLEDGED
    assert orders.orders[view2.order_id].status is OrderStatus.ACKNOWLEDGED


# ---- 실패 주입 2/3: inbox_processor — counter() 실패가 F9 중복 흡수를 막지 않는다 --


async def test_inbox_processor_duplicate_absorption_survives_metrics_counter_failure() -> None:
    """중복 이벤트 카운터(`safe_counter`)가 예외를 던져도 `ingest()`는
    예외 없이 `False`를 반환한다(트랜잭션 롤백·호출부 전파 없음) — F9(중복
    흡수)가 계측 장애로 깨지지 않음을 증명한다."""
    processor = InboxProcessor(
        FakePool(), inbox_repo=_AlwaysDuplicateInboxRepo(), metrics=_RaisingMetrics()
    )

    result = await processor.ingest(_provider_event())

    assert result is False


# ---- 실패 주입 3/3: unknown_resolver — observe() 실패가 RESOLVED_AS 반환을 막지 않는다


async def test_unknown_resolver_returns_resolved_order_despite_metrics_observe_failure() -> None:
    """`observe()`가 예외를 던져도 `resolve_unknown()`은 예외를 전파하지
    않고 이미 커밋된 RESOLVED_AS 결과를 그대로 반환한다(`safe_observe`) —
    `restart_recovery.py`의 배경 복구 루프가 계측 장애 하나로 나머지 UNKNOWN
    주문 처리를 멈추지 않는다는 근거."""
    view = _order_view()
    repo = _SingleOrderRepo(view)
    adapter = _FoundAdapter(_found_order())
    risk_gate_repo: RiskGateRepository = _UnusedRiskGateRepo()

    result = await resolve_unknown(
        view.order_id,
        adapter=adapter,
        pool=_UnknownFakePool(),
        risk_gate_repo=risk_gate_repo,
        clock=lambda: datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        order_repo=repo,
        metrics=_RaisingMetrics(),
    )

    assert result.status is OrderStatus.ACKNOWLEDGED


# ---- 적대적: 합성 예외가 아니라 실제 PrometheusMetrics 실패 모드로 재현 ------------


def test_safe_hooks_absorb_real_prometheus_relabel_failure_but_adapter_stays_fail_closed() -> None:
    """합성 예외 대신 `PrometheusMetrics`가 실제로 던지는 실패 모드(같은
    이름을 다른 라벨 키 집합으로 재등록 시 `ValueError`,
    `tests/unit/core/observability/test_metrics_port.py::
    test_prometheus_metrics_{counter,observe}_rejects_relabeling_same_name`와
    동일 근거)를 그대로 재현한다. 그 테스트가 명시하듯 어댑터 자체는 이
    실패를 흡수하지 않는 게 fail-closed 설계다 — 흡수 경계는 오직 OMS
    호출부(`safe_counter`/`safe_observe`)에만 있어야 한다는 I-10
    배선-증명 경계를 같은 실패로 양쪽 다 확인한다."""
    adapter = PrometheusMetrics()
    name_c = "aios.test.oms_l427_adversarial.count_total"
    name_o = "aios.test.oms_l427_adversarial.duration_seconds"

    adapter.counter(name_c, {"a": "1"})
    with pytest.raises(ValueError):
        adapter.counter(name_c, {"b": "2"})  # 어댑터 자체는 그대로 던진다(fail-closed)
    safe_counter(adapter, name_c, {"b": "2"})  # 호출부 경계에서는 흡수된다

    adapter.observe(name_o, 0.1, {"a": "1"})
    with pytest.raises(ValueError):
        adapter.observe(name_o, 0.2, {"b": "2"})
    safe_observe(adapter, name_o, 0.2, {"b": "2"})


# ---- 성능 단언: §7.3 로그 필드 JSON 직렬화 p95 -----------------------------------


@pytest.mark.perf
def test_dispatch_log_extra_json_serialization_meets_borrowed_latency_budget() -> None:
    """ADR-2026-09-09-C Decision 1 예산표에 로그 필드 직렬화 전용 항목이
    없어 가장 가까운 유사 항목("사전거래 게이트 p99 5ms")을 자체 예산으로
    차용한다(task-3979/5239e273, task-3367 DEEPEN과 동일 차용 근거) —
    `outbox_dispatcher`가 매 실패/거부 시 실제로 쓰는 §7.3 payload
    (`_dispatch_log_extra` 형태)를 실제 프로덕션 포매터(`JSONLinesFormatter`)로
    500회 직렬화한 p95 지연이 그 예산 안에 들어와야 한다 — PLT-03 "로그
    sink 지연이 거래 경로를 막지 않는다" 설계 의도가 필드 직렬화 단계에서도
    실측으로 성립함을 확인한다."""
    row = OutboxRow(
        id=uuid4(),
        order_id=uuid4(),
        command_type="SUBMIT",
        payload={},
        state="SENDING",
        attempt=3,
        not_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
        lease_until=None,
        worker_id="w1",
        last_error=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    extra = _dispatch_log_extra("oms.outbox.dispatch_failed", row, worker_id="w1")
    formatter = JSONLinesFormatter()
    perf_logger = logging.getLogger("test.oms.perf.dispatch_log")
    samples = 500
    budget_sec = 0.005  # 사전거래 게이트 p99 5ms 예산(ADR Decision 1) 차용

    latencies: list[float] = []
    for _ in range(samples):
        record = perf_logger.makeRecord(
            perf_logger.name,
            logging.ERROR,
            __file__,
            0,
            "outbox_dispatcher: outbox_id=%s 처리 실패",
            (),
            None,
            extra=extra,
        )
        start = time.perf_counter()
        formatter.format(record)
        latencies.append(time.perf_counter() - start)
    latencies.sort()
    p95 = latencies[int(samples * 0.95) - 1]

    print(
        f"[L4-27 oms] dispatch_log_extra JSON 직렬화 x{samples} "
        f"p95={p95 * 1000:.4f}ms (budget<{budget_sec * 1000:.0f}ms)"
    )
    assert p95 < budget_sec, f"로그 직렬화 p95 예산({budget_sec}s) 초과: {p95:.6f}s"


# ---- 명명 규칙 게이트 적색 재현 교차 참조 -----------------------------------------
#
# tests/unit/oms/test_metrics_names.py::
# test_naming_regex_rejects_spec_literal_4segment_names가 §7.2 원문(4/2세그먼트)
# 이름을 정규식이 실제로 거부함을 이미 재현한다(반증 테스트) — 여기서 중복
# 대신 교차 참조만 남긴다(c1465719/task-3367 DEEPEN과 동일 관례).
