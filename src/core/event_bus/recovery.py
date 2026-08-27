"""4.6 — 재시작 복구 절차.

Spec: 05_communication_architecture_v1.2.md#§5.6

편차: 원래 이 절차는 orders 테이블 조회(DB 세션 계층, 작업트리 16번)와
ExchangeAdapter.get_order()(작업트리 6번)가 필요하지만, 둘 다 이 시점
(작업트리 4번)에는 아직 없다. 순수 오케스트레이션 함수로 만들어 이 세 가지를
콜백으로 주입받도록 설계했다 — 해당 섹션이 완성되면 실제 구현을 넘겨주기만
하면 된다(Event Bus 자체를 인터페이스 뒤에 숨기는 §5.1 원칙과 동일한 방식).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# orders.status가 아직 최종 상태(FILLED/CANCELLED/REJECTED/EXPIRED/FAILED)가
# 아닌 행들을 반환 — 7.5 UNKNOWN 처리 원칙과 동일한 조회 로직 재사용 대상.
FetchPendingOrders = Callable[[], Awaitable[list[dict[str, Any]]]]
GetOrderStatus = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RepublishOrderEvent = Callable[[dict[str, Any]], Awaitable[None]]
RecordRecovery = Callable[[int], Awaitable[None]]


async def recover_pending_orders(
    *,
    fetch_pending_orders: FetchPendingOrders,
    get_order_status: GetOrderStatus,
    republish_order_event: RepublishOrderEvent,
    record_recovery: RecordRecovery | None = None,
) -> int:
    """프로세스 시작 시 1회 호출.

    §5.6 원칙 — "DB 쓰기가 이벤트 발행보다 먼저 일어난다. 이벤트가 유실되어도
    진실은 항상 DB에 있다"에 따라, 재시작으로 유실됐을 수 있는 이벤트를 DB
    상태 기준으로 재발행한다. 반환값은 재동기화한 건수(감사 추적용, §5.6 —
    "재시작 후 몇 건을 재동기화했는지 항상 추적 가능하게 한다").
    """
    pending = await fetch_pending_orders()
    recovered = 0
    for order in pending:
        try:
            current = await get_order_status(order)
        except Exception:
            logger.exception(
                "재시작 복구 중 주문 상태 재확인 실패: order_id=%s", order.get("order_id")
            )
            continue
        await republish_order_event(current)
        recovered += 1

    logger.info("재시작 복구 완료: %d건 재동기화", recovered)
    if record_recovery is not None:
        await record_recovery(recovered)
    return recovered
