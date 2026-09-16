"""FD-3.4 폴링 체결 반영 — `submit.py`(FD-4.2 주문 전송)에서 책임 분리
(task-4006, P6 LOC 분할, 로직 이동만·순서/불변식 불변).

Spec: 기능설계문서_v1.21.md#FD-4.2 (apply_fill 관련 절)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.base import Currency, Money
from src.data.models.trading import Order
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from src.services.order_service import repository
from src.services.order_service.types import PublishFn


async def apply_fill(
    order: Order,
    *,
    exchange_order_id: str,
    filled_quantity: Any,
    average_fill_price: Any,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    metrics: MetricsPort | None = None,
) -> Order:
    """제출 직후(동기 체결) 또는 이후 폴링(FD-3.4)으로 체결이 확인됐을 때
    상태를 FILLED로 갱신한다 — Executor.execute()와 실행 루프(오케스트레이터)
    양쪽이 공유하는 갱신 경로(FD-8.4 처리단계 5의 전제).

    task-1553(L4-15) — 실제 처리는 `oms.application.inbox_processor.
    InboxProcessor.ingest()`로 위임하는 얇은 호환 래퍼로 축소됐다(기존
    호출자 시그니처는 그대로). 폴링에는 거래소 원장의 개별 체결 내역이
    없으므로(누적 filled_quantity/average_fill_price 스냅샷뿐) `fills.
    provider_fill_id`를 `order_id`+`filled_quantity`의 결정론적 함수로
    합성한다 — 같은 스냅샷의 재호출(재시도·중복 폴링)은 항상 같은 키가 되어
    inbox/fills 양쪽의 ON CONFLICT가 흡수한다(§6 F9와 동일 보장)."""
    # 지연 임포트 — inbox_processor.py가 `order_service.repository`/
    # `position_ledger`를 쓰므로(§`_apply_position_ledger`), 모듈 최상단에서
    # 임포트하면 order_service/__init__.py → apply_fill.py → inbox_processor.py →
    # order_service(패키지) 순환 임포트가 된다.
    from src.services.oms.application.inbox_processor import InboxProcessor

    metrics = metrics if metrics is not None else NullMetrics()
    filled_qty = Decimal(str(filled_quantity))
    if isinstance(average_fill_price, Money):
        price_amount, fee_currency = average_fill_price.amount, average_fill_price.currency.value
    elif average_fill_price is not None:
        price_amount, fee_currency = Decimal(str(average_fill_price)), Currency.USDT.value
    else:
        price_amount, fee_currency = Decimal("0"), Currency.USDT.value

    now = datetime.now(timezone.utc)
    provider_id = f"poll:{order.order_id}:{filled_qty}"
    fill = FillEvent(
        provider_fill_id=provider_id,
        venue=order.exchange,
        order_id=order.order_id,
        exchange_order_id=exchange_order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=filled_qty,
        price=price_amount,
        fee=Decimal("0"),
        fee_currency=fee_currency,
        liquidity="UNKNOWN",
        venue_ts=now,
    )
    ev = ProviderOrderEvent(
        provider_event_id=provider_id,
        venue=order.exchange,
        venue_symbol=order.symbol,
        exchange_order_id=exchange_order_id,
        client_order_id=order.client_order_id,
        venue_status="FILLED",
        filled_quantity=filled_qty,
        average_price=price_amount,
        last_fill=fill,
        venue_ts=now,
        received_at=now,
        source="POLL",
        raw_hash=hashlib.sha256(provider_id.encode()).hexdigest(),
    )
    await InboxProcessor(pool, metrics=metrics).ingest(ev)

    async with pool.acquire() as conn:
        persisted = await repository.get_by_order_id(conn, order.order_id)
    if persisted is None:
        raise RuntimeError(
            f"apply_fill: order_id={order.order_id} 조회 실패 — inbox 처리 후 행이 없습니다."
        )

    if publish is not None:
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
