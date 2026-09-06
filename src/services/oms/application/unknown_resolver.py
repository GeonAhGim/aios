"""L4-16 — UNKNOWN 주문 해소(§4.2 F5-a, §6 F5-a).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`application/unknown_resolver.py`, §4.2 UNKNOWN 행 3종
(`RESOLVED_AS`/`RESOLVED_ABSENT`/`UNRESOLVED_LIMIT`), §6 F5-a, §9 L4-16.

이 리프는 F5-a(client id를 지원하는 venue — Bitget)만 다룬다. F5-b(KIS/NH,
client id 미지원)는 `find_order_by_client_id`가 `UnsupportedCapabilityError`로
fail-closed 거부하도록 두고(어댑터 ABC 기본 계약, L4-13) 잡지 않는다 — 이
venue는 L4-21이 별도 경로(`get_open_orders`+`get_order_history` 매칭)를
구현할 때까지 자동 해소하지 않는다(decision: "미지원 거래소는
UnsupportedCapabilityError로 fail-closed").

시도마다 `adapter.find_order_by_client_id(order.client_order_id)`를 부른다:
- 찾으면(`Order` 반환) 그 주문의 실제 상태로 `RESOLVED_AS(x)` 전이(§4.2) —
  x는 `ALLOWED[UNKNOWN]`(state_machine.py L4-02)이 허용하는 상태여야 한다
  (아니면 어댑터 계약 위반 신호로 `InvalidOrderTransitionError`, fail-closed).
- 못 찾으면(`None`) NOT_FOUND로 집계한다. `ORDER_NOT_FOUND` 연속 2회 +
  미체결 목록에도 없음(`get_open_orders`) + `unknown_since` 경과 ≥120초를
  모두 만족해야만 `RESOLVED_ABSENT`(→FAILED) — 셋 중 하나라도 미달이면
  계속 재시도한다(§4.2 "둘 중 하나만 충족하면 유지"의 3조건 버전).
- `max_attempts`(기본 5) 소진 후에도 미해소면 `UNRESOLVED_LIMIT`(자기루프,
  상태 불변) + `activate_safety_control(scope=ACCOUNT)`(I12, U15 — ACCOUNT
  범위로 건다) — 자동으로 FAILED 확정하지 않는다(fail-closed, DoD "재시도
  상한 초과 → safety control 발생, 자동 FAILED 처리 금지").

시각은 주입 `clock`(Callable[[], datetime])만 쓴다 — `unknown_since` 경과
판정과 이벤트 `occurred_at`이 전부 이 값에서 나온다. `sleep`도 주입값이라
테스트는 실시간 대기 없이 backoff 스텝 수만 단언한다(test_split_brain
d3227c9 선례와 동일 원칙, 모듈 docstring이 아니라 여기 실제로 지킨다).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.state_machine import ALLOWED, OrderEvent, next_status
from src.services.oms.ports.repository import OrderRepoPort

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]

DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
NOT_FOUND_STREAK_THRESHOLD = 2
ABSENT_ELAPSED_SECONDS = 120.0

_orders = PostgresOrderRepository()


def _payload_hash(order_id: UUID, tag: str) -> str:
    canonical = json.dumps({"order_id": str(order_id), "tag": tag}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _is_still_open(adapter: ExchangeAdapter, order: OrderView) -> bool:
    open_orders = await adapter.get_open_orders(order.symbol)
    return any(
        o.client_order_id == order.client_order_id
        or (order.exchange_order_id is not None and o.exchange_order_id == order.exchange_order_id)
        for o in open_orders
    )


async def _apply_resolved_as(
    pool: asyncpg.Pool, repo: OrderRepoPort, order_id: UUID, found: Order, *, clock: Clock
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            current = await repo.get_for_update(conn, order_id)
            if current.status is not OrderStatus.UNKNOWN:
                result = current  # 이미 다른 경로로 해소됨(경합) — 멱등 반환
            else:
                target = found.status
                if target not in ALLOWED[OrderStatus.UNKNOWN]:
                    raise InvalidOrderTransitionError(
                        f"find_order_by_client_id가 돌려준 status={target.value}는 "
                        "UNKNOWN에서 허용되는 목적지가 아닙니다 — 어댑터 계약 위반."
                    )
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=OrderStatus.UNKNOWN,
                    to_status=target,
                    event=OrderEvent.RESOLVED_AS.value,
                    reason_code="PROVIDER_LOOKUP_MATCHED",
                    actor_subject_id="system",
                    trace_id=uuid4(),
                    command_id=None,
                    provider_event_id=None,
                    occurred_at=clock(),
                    payload_hash=_payload_hash(order_id, f"RESOLVED_AS:{target.value}"),
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.UNKNOWN,
                    expected_version=current.version,
                    new_status=target,
                    patch={
                        "filled_quantity": found.filled_quantity,
                        "exchange_order_id": found.exchange_order_id,
                    },
                    event=event,
                )
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()
    return result


async def _apply_resolved_absent(
    pool: asyncpg.Pool, repo: OrderRepoPort, order_id: UUID, *, clock: Clock
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            current = await repo.get_for_update(conn, order_id)
            if current.status is not OrderStatus.UNKNOWN:
                result = current
            else:
                target = next_status(current.status, OrderEvent.RESOLVED_ABSENT)
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=OrderStatus.UNKNOWN,
                    to_status=target,
                    event=OrderEvent.RESOLVED_ABSENT.value,
                    reason_code="NOT_AT_PROVIDER",
                    actor_subject_id="system",
                    trace_id=uuid4(),
                    command_id=None,
                    provider_event_id=None,
                    occurred_at=clock(),
                    payload_hash=_payload_hash(order_id, "RESOLVED_ABSENT"),
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.UNKNOWN,
                    expected_version=current.version,
                    new_status=target,
                    patch={},
                    event=event,
                )
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()
    return result


async def _escalate(
    pool: asyncpg.Pool,
    repo: OrderRepoPort,
    risk_gate_repo: RiskGateRepository,
    order_id: UUID,
    *,
    max_attempts: int,
    clock: Clock,
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        should_activate = False
        try:
            current = await repo.get_for_update(conn, order_id)
            if current.status is not OrderStatus.UNKNOWN:
                result = current  # 경합 중 이미 해소됨 — 안전통제를 걸 이유가 없다
            else:
                target = next_status(current.status, OrderEvent.UNRESOLVED_LIMIT)
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=OrderStatus.UNKNOWN,
                    to_status=target,
                    event=OrderEvent.UNRESOLVED_LIMIT.value,
                    reason_code="UNKNOWN_RESOLUTION_ATTEMPTS_EXHAUSTED",
                    actor_subject_id="system",
                    trace_id=uuid4(),
                    command_id=None,
                    provider_event_id=None,
                    occurred_at=clock(),
                    payload_hash=_payload_hash(order_id, "UNRESOLVED_LIMIT"),
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.UNKNOWN,
                    expected_version=current.version,
                    new_status=target,
                    patch={},
                    event=event,
                )
                should_activate = True
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()

    if should_activate:
        await activate_safety_control(
            risk_gate_repo,
            tenant_id=result.tenant_id,
            actor_subject_id=result.tenant_id,
            actor_is_admin=True,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(result.tenant_id),
            reason=f"OMS_UNKNOWN_ORDER_UNRESOLVED:{order_id}",
        )
        logger.critical(
            "unknown_resolver: order_id=%s 상한(%d회) 초과 — ACCOUNT safety control ACTIVE",
            order_id,
            max_attempts,
        )
    return result


async def resolve_unknown(
    order_id: UUID,
    *,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    risk_gate_repo: RiskGateRepository,
    clock: Clock,
    sleep: SleepFn = asyncio.sleep,
    order_repo: OrderRepoPort | None = None,
    max_attempts: int = 5,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF,
) -> OrderView:
    if risk_gate_repo is None:  # I-01 — 안전 게이트 인자는 None 기본값을 갖지 않는다
        raise TypeError(
            "risk_gate_repo는 필수입니다(I-01) — None을 명시적으로 넘길 수 없습니다."
        )
    repo = order_repo if order_repo is not None else _orders

    async with pool.acquire() as conn:
        order = await repo.get_for_update(conn, order_id)
    if order.status is not OrderStatus.UNKNOWN:
        return order  # 이미 해소됨 — 재작업 없이 멱등 반환
    if order.unknown_since is None:
        raise ValueError(
            f"order_id={order_id}: UNKNOWN 상태인데 unknown_since가 없습니다 — 데이터 결함."
        )

    not_found_streak = 0
    for attempt in range(1, max_attempts + 1):
        found = await adapter.find_order_by_client_id(order.client_order_id)
        if found is not None:
            return await _apply_resolved_as(pool, repo, order_id, found, clock=clock)

        not_found_streak += 1
        elapsed = (clock() - order.unknown_since).total_seconds()
        still_open = await _is_still_open(adapter, order)
        if (
            not still_open
            and not_found_streak >= NOT_FOUND_STREAK_THRESHOLD
            and elapsed >= ABSENT_ELAPSED_SECONDS
        ):
            return await _apply_resolved_absent(pool, repo, order_id, clock=clock)

        if attempt < max_attempts:
            await sleep(backoff[min(attempt - 1, len(backoff) - 1)])

    return await _escalate(
        pool, repo, risk_gate_repo, order_id, max_attempts=max_attempts, clock=clock
    )
