"""L4-27 — §7.2/§7.3 계측 배선 증명(FakeMetrics) + §7.3 로그 필드 negative test.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.2, §7.3, §9 L4-27 DoD(c)/(e).

세 계측 지점을 실DB 없이(포트 대역) 정확히 1회 호출됨을 단언한다 — 소스의
계측 호출을 지우면 이 파일의 해당 테스트가 실패한다. 로그 negative test는
PLT-02 `RedactionFilter`가 이 모듈들이 실제로 쓰는 `extra={"payload": {...}}`
채널에 마스킹을 적용함을 확인한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from src.core.logging.redaction import REDACTED, RedactionFilter
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


# ---- outbox_dispatcher: aios.oms.outbox_dispatch.duration_seconds -----------------


async def test_outbox_dispatcher_observes_dispatch_duration_once_per_row() -> None:
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    view = orders.add(make_order_view())
    await enqueue(outbox, view)
    adapter = ScriptedAdapter()
    spy = _SpyMetrics()
    dispatcher = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock,
                                  metrics=spy)

    report = await dispatcher.dispatch_once()

    assert report.acknowledged == 1
    durations = [o for o in spy.observations if o[0] == OMS_OUTBOX_DISPATCH_DURATION_SECONDS]
    assert len(durations) == 1
    _, value, labels = durations[0]
    assert value >= 0.0
    assert labels == {"venue": view.exchange, "command_type": "SUBMIT"}


# ---- inbox_processor: aios.oms.inbox_duplicate.count_total ------------------------


class _AlwaysDuplicateInboxRepo:
    async def insert_if_absent(self, conn: object, ev: ProviderOrderEvent) -> bool:
        return False

    async def claim_unprocessed(self, conn: object, *, limit: int) -> list[Any]:
        raise NotImplementedError

    async def mark_processed(self, conn: object, id: UUID, *, expected_state: str = "NEW") -> None:
        raise NotImplementedError


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


async def test_inbox_processor_counts_duplicate_event_once() -> None:
    spy = _SpyMetrics()
    processor = InboxProcessor(
        _FakePool(),  # 중복 경로는 pool.acquire()만 쓴다
        inbox_repo=_AlwaysDuplicateInboxRepo(),
        metrics=spy,
    )

    result = await processor.ingest(_provider_event())

    assert result is False
    assert spy.counters == [(OMS_INBOX_DUPLICATE_COUNT_TOTAL, {"venue": "bitget", "source": "WS"})]


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


# ---- unknown_resolver: aios.oms.unknown_resolution.duration_seconds ---------------


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


class _SingleOrderRepo:
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
    """`RiskGateRepository` 구조를 만족하는 스텁 — RESOLVED_AS 경로는 이
    포트를 실제로 호출하지 않으므로 모든 메서드가 NotImplementedError를
    던진다(호출되면 테스트가 그 자리에서 실패해 가정이 깨졌음을 드러낸다)."""

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


def _order_view() -> OrderView:
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


async def test_unknown_resolver_observes_resolution_duration_once() -> None:
    view = _order_view()
    repo = _SingleOrderRepo(view)
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
    durations = [o for o in spy.observations if o[0] == OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS]
    assert len(durations) == 1
    _, value, labels = durations[0]
    assert value >= 0.0
    assert labels == {"outcome": "RESOLVED_AS"}


# ---- §7.3 negative test: 금지 필드는 RedactionFilter로 마스킹된다 -----------------


def test_redaction_filter_masks_forbidden_fields_in_oms_log_payload() -> None:
    """세 모듈이 실제로 쓰는 `extra={"event":..., "payload": {...}}` 채널에
    금지 필드(raw payload/API 키)가 섞여도 `RedactionFilter`가 마스킹한다 —
    order_id/outbox_id 같은 §7.3 도메인 필드는 그대로 남아야 한다."""
    logger = logging.getLogger("test.oms.redaction")
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 0, "outbox_dispatcher: dispatch",
        (), None,
        extra={
            "event": "oms.outbox.dispatch_failed",
            "payload": {
                "order_id": "11111111-1111-1111-1111-111111111111",
                "outbox_id": "22222222-2222-2222-2222-222222222222",
                "attempt": 3,
                "raw_payload": {"order": "secret body"},
                "api_key": "sk-live-abcdef",
            },
        },
    )

    assert RedactionFilter().filter(record) is True

    payload = getattr(record, "payload", {})
    assert payload["order_id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["outbox_id"] == "22222222-2222-2222-2222-222222222222"
    assert payload["attempt"] == 3
    assert payload["raw_payload"] == REDACTED
    assert payload["api_key"] == REDACTED
