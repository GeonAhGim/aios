"""FD-4.5 — UNKNOWN 상태 재조회(호환 래퍼).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`order_service/reconcile.py`(§9 L4-16 "위임") — 실제 판정·전이는
`oms.application.unknown_resolver.resolve_unknown`(F5-a: `find_order_by_client_id`
역조회, `ORDER_NOT_FOUND` 2회+120s → FAILED, 상한 초과 → ACCOUNT safety
control)로 위임한다. 이 모듈은 기존 호출자 시그니처(`Order` 반환, `publish`/
`metrics` 옵션)를 유지하는 얇은 어댑터일 뿐이다.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.observability.metric_names import ORDER_UNKNOWN_STATE_GAUGE
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.oms.application import unknown_resolver
from src.services.order_service import repository
from src.services.order_service.submit import PublishFn

SleepFn = Callable[[float], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_unknown(
    order_id: UUID,
    *,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    sleep: SleepFn = asyncio.sleep,
    metrics: MetricsPort | None = None,
) -> Order:
    metrics = metrics if metrics is not None else NullMetrics()

    view = await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=_utcnow,
        sleep=sleep,
    )

    metrics.gauge(
        ORDER_UNKNOWN_STATE_GAUGE,
        0.0 if view.status is not OrderStatus.UNKNOWN else 1.0,
        labels={"exchange": view.exchange},
    )

    async with pool.acquire() as conn:
        persisted = await repository.get_by_order_id(conn, order_id)
    if persisted is None:
        raise RuntimeError(
            f"resolve_unknown: order_id={order_id} 조회 실패 — 해소 처리 후 행이 없습니다."
        )

    if publish is not None and view.status is not OrderStatus.UNKNOWN:
        await publish(
            "order.status.changed",
            {
                "order_id": str(persisted.order_id),
                "client_order_id": persisted.client_order_id,
                "execution_id": persisted.execution_id,
                "status": persisted.status.value,
            },
        )
    return persisted
