"""L4-14 outbox 디스패처 테스트용 포트 대역(메모리) — §5.1 SQL 의미론의 모델.

Postgres 어댑터(L4-08)와 `order_command_outbox`/`order_events` 스키마(L4-06)가
아직 없어(task-1538 note) 디스패처는 포트(`OutboxRepoPort`/`OrderRepoPort`)에
대해서만 증명한다. 이 대역이 지키는 계약:

- `claim_batch`는 await 없이 한 번에 실행된다 — asyncio 단일 스레드에서 `FOR
  UPDATE SKIP LOCKED`와 같은 원자성(두 워커가 같은 행을 받을 수 없음).
- `mark_done/retry/dead`는 `state='SENDING' AND worker_id=expected_worker`가
  아니면 `ConcurrencyConflictError`(105번 §2 RETURNING 0행).
- `transition`은 expected_status·expected_version 불일치 시 같은 예외, 전이표
  (`ALLOWED`) 밖이면 `InvalidOrderTransitionError`, version +1, 이벤트 1행.
- `FakeConn.transaction()`은 예외 시 그 트랜잭션 안의 쓰기를 전부 되돌린다 —
  "펜스 먼저, 전이 나중"이 같은 tx라는 디스패처 불변을 검증할 수 있다.

실DB 변형(3워커 SKIP LOCKED·늦은 쓰기 RETURNING 0행)은 L4-06/08 이후
`tests/integration/oms/`에 같은 케이스로 추가한다.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
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
from src.services.oms.ports.repository import CommandType, OutboxRow
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

Undo = Callable[[], None]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# ---- 커넥션/트랜잭션 대역 -------------------------------------------------------
class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeTransaction:
        self._conn.frames.append([])
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        frame = self._conn.frames.pop()
        if exc_type is not None:
            for undo in reversed(frame):
                undo()
        elif self._conn.frames:
            self._conn.frames[-1].extend(frame)
        return False


class FakeConn:
    def __init__(self) -> None:
        self.frames: list[list[Undo]] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def on_rollback(self, undo: Undo) -> None:
        if self.frames:
            self.frames[-1].append(undo)


class _Acquire:
    def __init__(self) -> None:
        self._conn = FakeConn()

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakePool:
    def acquire(self) -> _Acquire:
        return _Acquire()


# ---- outbox 대역 ------------------------------------------------------------------
class InMemoryOutboxRepo:
    def __init__(self, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.rows: dict[UUID, OutboxRow] = {}
        self._clock = clock
        self.claim_calls = 0

    async def enqueue(
        self,
        conn: FakeConn,
        *,
        order_id: UUID,
        command_type: CommandType,
        payload: dict[str, Any],
        not_before: datetime,
    ) -> UUID:
        row_id = uuid4()
        now = self._clock()
        self._put(
            conn,
            OutboxRow(
                id=row_id, order_id=order_id, command_type=command_type, payload=payload,
                state="PENDING", attempt=0, not_before=not_before, lease_until=None,
                worker_id=None, last_error=None, created_at=now, updated_at=now,
            ),
        )
        return row_id

    async def claim_batch(
        self, conn: FakeConn, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        # await 없음 — SKIP LOCKED 원자성 모델(모듈 docstring).
        self.claim_calls += 1
        now = self._clock()
        candidates = sorted(
            (r for r in self.rows.values() if r.state == "PENDING" and r.not_before <= now),
            key=lambda r: r.created_at,
        )[:limit]
        claimed: list[OutboxRow] = []
        for row in candidates:
            new = row.model_copy(
                update={
                    "state": "SENDING", "worker_id": worker_id,
                    "lease_until": now + timedelta(seconds=lease_sec), "updated_at": now,
                }
            )
            self._put(conn, new)
            claimed.append(new)
        return claimed

    async def mark_done(self, conn: FakeConn, id: UUID, *, expected_worker: str) -> None:
        row = self._fenced(id, expected_worker)
        self._put(conn, row.model_copy(update={"state": "DONE", "updated_at": self._clock()}))

    async def mark_retry(
        self, conn: FakeConn, id: UUID, *, attempt: int, not_before: datetime,
        last_error: str, expected_worker: str,
    ) -> None:
        row = self._fenced(id, expected_worker)
        self._put(
            conn,
            row.model_copy(
                update={
                    "state": "PENDING", "attempt": attempt, "not_before": not_before,
                    "last_error": last_error, "worker_id": None, "lease_until": None,
                    "updated_at": self._clock(),
                }
            ),
        )

    async def mark_dead(
        self, conn: FakeConn, id: UUID, *, reason: str, expected_worker: str
    ) -> None:
        row = self._fenced(id, expected_worker)
        self._put(
            conn,
            row.model_copy(
                update={"state": "DEAD", "last_error": reason, "updated_at": self._clock()}
            ),
        )

    def force(self, id: UUID, **update: Any) -> None:
        """테스트 전용 — 복구 워커/다른 프로세스의 쓰기를 흉내 낸다."""
        self.rows[id] = self.rows[id].model_copy(update=update)

    def _fenced(self, id: UUID, expected_worker: str) -> OutboxRow:
        row = self.rows.get(id)
        if row is None or row.state != "SENDING" or row.worker_id != expected_worker:
            raise ConcurrencyConflictError(
                f"outbox {id}: state={row.state if row else None} "
                f"worker={row.worker_id if row else None} expected={expected_worker}"
            )
        return row

    def _put(self, conn: FakeConn, row: OutboxRow) -> None:
        prev = self.rows.get(row.id)

        def undo(prev: OutboxRow | None = prev, row_id: UUID = row.id) -> None:
            if prev is None:
                self.rows.pop(row_id, None)
            else:
                self.rows[row_id] = prev

        self.rows[row.id] = row
        conn.on_rollback(undo)


# ---- orders 대역 ------------------------------------------------------------------
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


# ---- 어댑터 대역 ------------------------------------------------------------------
PlaceHook = Callable[[Order], Awaitable[Order]]


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


# ---- 데이터 빌더 ------------------------------------------------------------------
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
        order_id=uuid4(), tenant_id=tenant_id or uuid4(), execution_id=7,
        client_order_id=client_order_id or f"a{uuid4().hex[:20]}",
        exchange_order_id=exchange_order_id, symbol="BTC/USDT", venue_symbol="BTCUSDT",
        exchange=exchange, side=OrderSide.BUY, order_type=OrderType.MARKET,
        time_in_force="GTC", quantity=Decimal("0.5"), price=None, status=status,
        filled_quantity=Decimal("0"), average_fill_price=None, fee_total=None,
        fee_currency=None, version=version, parent_order_id=None, algo_run_id=None,
        unknown_since=None, provider_order_date=None, created_at=now, updated_at=now,
    )


def make_venue_order(view: OrderView, *, client_order_id: str | None = None) -> Order:
    return Order(
        order_id=view.order_id, client_order_id=client_order_id or view.client_order_id,
        strategy_id="s1", strategy_version="1.0.0", execution_id=view.execution_id,
        symbol=view.symbol, exchange=view.exchange, side=view.side,
        order_type=view.order_type, quantity=view.quantity, price=None,
        status=OrderStatus.VALIDATED, asset_class=AssetClass.CRYPTO,
    )


def submit_payload(view: OrderView, *, client_order_id: str | None = None) -> dict[str, Any]:
    return {
        "order": make_venue_order(view, client_order_id=client_order_id).model_dump(mode="json"),
        "trace_id": str(uuid4()),
        "command_id": str(uuid4()),
    }


async def enqueue(
    outbox: InMemoryOutboxRepo,
    view: OrderView,
    *,
    command_type: CommandType = "SUBMIT",
    payload: dict[str, Any] | None = None,
    not_before: datetime | None = None,
) -> UUID:
    body = payload if payload is not None else submit_payload(view)
    return await outbox.enqueue(
        FakeConn(), order_id=view.order_id, command_type=command_type, payload=body,
        not_before=not_before or datetime(2000, 1, 1, tzinfo=timezone.utc),
    )


async def allow_gate(ctx: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


async def deny_gate(ctx: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.DENY, reason_codes=("KILL_SWITCH_ACTIVE",))


async def _no_sleep(seconds: float) -> None:
    return None


def make_dispatcher(
    *,
    outbox: InMemoryOutboxRepo,
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
        FakePool(),  # type: ignore[arg-type]
        outbox_repo=outbox, order_repo=orders, resolve_adapter=resolve,
        pre_send_gate=gate, worker_id=worker_id, clock=clock, rng=lambda: 0.5,
        **kwargs,
    )


def gate_param_has_no_default() -> bool:
    """I-01 — `pre_send_gate`는 기본값이 없어야 한다(정적 검사와 같은 판정)."""
    param = inspect.signature(OutboxDispatcher.__init__).parameters["pre_send_gate"]
    return param.default is inspect.Parameter.empty
