"""L4-16 — UNKNOWN order resolution (§4.2 F5-a, §6 F5-a) + L4-27 observability instrumentation.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`application/unknown_resolver.py`, §4.2 UNKNOWN 행 3종
(`RESOLVED_AS`/`RESOLVED_ABSENT`/`UNRESOLVED_LIMIT`), §6 F5-a, §7.2, §9 L4-16/L4-27.

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

The three commit-write branches (`apply_resolved_as`/`apply_resolved_absent`/
`escalate`) were split out into `unknown_resolver_writes.py` (300-line cap,
see that module's docstring). `aios.oms.unknown_resolution.duration_seconds
{outcome}` is observed exactly once per resolution attempt (at the point the
whole retry loop finishes) — `outcome` is the final branch name
(RESOLVED_AS/RESOLVED_ABSENT/UNRESOLVED_LIMIT) verbatim.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

import asyncpg

from src.core.observability.metric_names import OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.trading import OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.unknown_resolver_writes import (
    apply_resolved_absent,
    apply_resolved_as,
    escalate,
)
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.ports.repository import OrderRepoPort

Clock = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]

DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
NOT_FOUND_STREAK_THRESHOLD = 2
ABSENT_ELAPSED_SECONDS = 120.0

_orders = PostgresOrderRepository()


async def _is_still_open(adapter: ExchangeAdapter, order: OrderView) -> bool:
    open_orders = await adapter.get_open_orders(order.symbol)
    return any(
        o.client_order_id == order.client_order_id
        or (order.exchange_order_id is not None and o.exchange_order_id == order.exchange_order_id)
        for o in open_orders
    )


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
    metrics: MetricsPort | None = None,
) -> OrderView:
    if risk_gate_repo is None:  # I-01 — 안전 게이트 인자는 None 기본값을 갖지 않는다
        raise TypeError(
            "risk_gate_repo는 필수입니다(I-01) — None을 명시적으로 넘길 수 없습니다."
        )
    repo = order_repo if order_repo is not None else _orders
    m = metrics if metrics is not None else NullMetrics()

    async with pool.acquire() as conn:
        order = await repo.get_for_update(conn, order_id)
    if order.status is not OrderStatus.UNKNOWN:
        return order  # 이미 해소됨 — 재작업 없이 멱등 반환
    if order.unknown_since is None:
        raise ValueError(
            f"order_id={order_id}: UNKNOWN 상태인데 unknown_since가 없습니다 — 데이터 결함."
        )

    start = time.monotonic()
    not_found_streak = 0
    for attempt in range(1, max_attempts + 1):
        found = await adapter.find_order_by_client_id(order.client_order_id)
        if found is not None:
            resolved = await apply_resolved_as(pool, repo, order_id, found, clock=clock)
            _observe(m, start, "RESOLVED_AS")
            return resolved

        not_found_streak += 1
        elapsed = (clock() - order.unknown_since).total_seconds()
        still_open = await _is_still_open(adapter, order)
        if (
            not still_open
            and not_found_streak >= NOT_FOUND_STREAK_THRESHOLD
            and elapsed >= ABSENT_ELAPSED_SECONDS
        ):
            resolved = await apply_resolved_absent(pool, repo, order_id, clock=clock)
            _observe(m, start, "RESOLVED_ABSENT")
            return resolved

        if attempt < max_attempts:
            await sleep(backoff[min(attempt - 1, len(backoff) - 1)])

    escalated = await escalate(
        pool, repo, risk_gate_repo, order_id, max_attempts=max_attempts, clock=clock
    )
    _observe(m, start, "UNRESOLVED_LIMIT")
    return escalated


def _observe(m: MetricsPort, start: float, outcome: str) -> None:
    elapsed = time.monotonic() - start
    m.observe(OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS, elapsed, {"outcome": outcome})
