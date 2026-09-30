"""Order repository and exchange adapter fakes for OMS outbox dispatcher tests.

This module contains fake implementations for:
- InMemoryOrderRepo: in-memory order repository
- ScriptedAdapter: scripted exchange adapter for testing
- Order/venue helpers: make_order_view, make_venue_order, etc.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.state_machine import ALLOWED
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.support.oms_outbox_fakes import FakeConn, FakePool

PlaceHook = Callable[[Order], Awaitable[Order]]


class InMemoryOrderRepo:
    def __init__(self) -> None:
        self.orders: dict[UUID, OrderView] = {}
        self.events: list[OrderTransitionEvent] = []

    def add(self, order: OrderView) -> OrderView:
        self.orders[order.order_id] = order
        return order

    def force(self, order_id: UUID, **update: Any) -> OrderView:
        """테스트 전용 — 복구/inbox 등 다른 경로의 전이(version +1)."""
        current = self.orders[order_id]
        new = current.model_copy(update={**update, "version": current.version + 1})
        self.orders[order_id] = new
        return new

    async def get_for_update(self, conn: FakeConn, order_id: UUID) -> OrderView:
        return self.orders[order_id]

    async def find_by_scope_hash(self, conn: FakeConn, scope_hash: str) -> OrderView | None:
        return None

    async def transition(
        self,
        conn: FakeConn,
        *,
        order_id: UUID,
        expected_status: OrderStatus,
        expected_version: int,
        new_status: OrderStatus,
        patch: dict[str, Any],
        event: OrderTransitionEvent,
    ) -> OrderView:
        current = self.orders[order_id]
        if current.status != expected_status or current.version != expected_version:
            raise ConcurrencyConflictError(
                f"order {order_id}: {current.status.value}/v{current.version} != "
                f"{expected_status.value}/v{expected_version}"
            )
        if new_status not in ALLOWED[current.status]:
            raise InvalidOrderTransitionError(f"{current.status.value} -> {new_status.value}")
        unknown = set(patch) - _PATCHABLE_COLUMNS
        if unknown:
            raise KeyError(f"patch에 미지 컬럼 {sorted(unknown)} — orders 실컬럼과 맞지 않는다")
        fields = {k: v for k, v in patch.items() if k in OrderView.model_fields}
        new = current.model_copy(
            update={**fields, "status": new_status, "version": current.version + 1}
        )
        self.orders[order_id] = new
        self.events.append(event.model_copy(update={"seq": len(self.events) + 1}))

        def undo(prev: OrderView = current) -> None:
            self.orders[order_id] = prev
            self.events.pop()

        conn.on_rollback(undo)
        return new


class ScriptedAdapter(FakeExchangeAdapter):
    """place_order/find_order_by_client_id/cancel/modify를 스크립트로 제어하고
    호출을 기록한다(DoD "어댑터 호출 1회" 단언용)."""

    def __init__(
        self,
        *,
        on_place: PlaceHook | None = None,
        lookup: dict[str, Order | None] | None = None,
        lookup_unsupported: bool = False,
        on_cancel: Callable[[str], Awaitable[bool]] | None = None,
        on_modify: Callable[..., Awaitable[Order]] | None = None,
    ) -> None:
        super().__init__(on_place_order=on_place)
        self._lookup = lookup or {}
        self._lookup_unsupported = lookup_unsupported
        self._on_cancel = on_cancel
        self._on_modify = on_modify
        self.calls: list[str] = []
        self.lookup_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.modify_calls: list[tuple[str, dict[str, Any]]] = []

    async def place_order(self, order: Order) -> Order:
        self.calls.append(order.client_order_id)
        return await super().place_order(order)

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        self.lookup_calls.append(client_order_id)
        if self._lookup_unsupported:
            raise self._unsupported("find_order_by_client_id")
        return self._lookup.get(client_order_id)

    async def cancel_order(self, order_id: str) -> bool:
        self.cancel_calls.append(order_id)
        if self._on_cancel is not None:
            return await self._on_cancel(order_id)
        return True

    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:
        self.modify_calls.append((order_id, kwargs))
        if self._on_modify is None:
            raise AssertionError("on_modify 스크립트 없음")
        return await self._on_modify(order_id, **kwargs)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_order_view(
    *,
    status: OrderStatus = OrderStatus.VALIDATED,
    tenant_id: UUID | None = None,
    client_order_id: str | None = None,
    exchange: str = "bitget",
    exchange_order_id: str | None = None,
    version: int = 1,
) -> OrderView:
    now = utcnow()
    return OrderView(
        order_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        execution_id=7,
        client_order_id=client_order_id or f"a{uuid4().hex[:20]}",
        exchange_order_id=exchange_order_id,
        symbol="BTC/USDT",
        venue_symbol="BTCUSDT",
        exchange=exchange,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force="GTC",
        quantity=Decimal("0.5"),
        price=None,
        status=status,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        fee_total=None,
        fee_currency=None,
        version=version,
        parent_order_id=None,
        algo_run_id=None,
        unknown_since=None,
        provider_order_date=None,
        created_at=now,
        updated_at=now,
    )


def make_venue_order(view: OrderView, *, client_order_id: str | None = None) -> Order:
    return Order(
        order_id=view.order_id,
        client_order_id=client_order_id or view.client_order_id,
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=view.execution_id,
        symbol=view.symbol,
        exchange=view.exchange,
        side=view.side,
        order_type=view.order_type,
        quantity=view.quantity,
        price=None,
        status=OrderStatus.VALIDATED,
        asset_class=AssetClass.CRYPTO,
    )


def submit_payload(view: OrderView, *, client_order_id: str | None = None) -> dict[str, Any]:
    return {
        "order": make_venue_order(view, client_order_id=client_order_id).model_dump(mode="json"),
        "trace_id": str(uuid4()),
        "command_id": str(uuid4()),
    }


# Patchable columns constant moved from oms_outbox_fakes.py
_PATCHABLE_COLUMNS = frozenset(OrderView.model_fields) | {"sent_at"}


# Dispatcher orchestration helpers (using both outbox and order repos)


async def allow_gate(ctx: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
    )


async def deny_gate(ctx: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.DENY, reason_codes=("KILL_SWITCH_ACTIVE",))


async def _no_sleep(seconds: float) -> None:
    return None


async def enqueue(
    outbox: InMemoryOutboxRepo,  # type: ignore
    view: OrderView,
    *,
    command_type: str = "SUBMIT",
    payload: dict[str, Any] | None = None,
    not_before: datetime | None = None,
) -> UUID:
    from tests.support.oms_outbox_fakes import FakeConn

    body = payload if payload is not None else submit_payload(view)
    return await outbox.enqueue(  # type: ignore[no-any-return]
        FakeConn(),
        order_id=view.order_id,
        command_type=command_type,
        payload=body,
        not_before=not_before or datetime(2000, 1, 1, tzinfo=timezone.utc),
    )


def make_dispatcher(
    *,
    outbox: InMemoryOutboxRepo,  # type: ignore
    orders: InMemoryOrderRepo,
    adapter: FakeExchangeAdapter,
    worker_id: str = "w1",
    gate: Callable[[OrderContext], Awaitable[GateDecision]] = allow_gate,
    clock: Callable[[], datetime] = utcnow,
    **kwargs: Any,
) -> OutboxDispatcher:
    async def resolve(tenant_id: UUID, exchange: str) -> FakeExchangeAdapter:
        return adapter

    kwargs.setdefault("sleep", _no_sleep)
    return OutboxDispatcher(
        FakePool(),
        outbox_repo=outbox,
        order_repo=orders,  # type: ignore[arg-type]
        resolve_adapter=resolve,
        pre_send_gate=gate,
        worker_id=worker_id,
        clock=clock,
        rng=lambda: 0.5,
        **kwargs,
    )
