"""읽기 전용 주문 조회 서비스 함수(L4 명세 §2-C, §9 L4-26).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`application/order_query.py`, §9 L4-26("서비스 함수까지 — 선행 07.
DoD: tenant 격리 테스트").

라우터/HTTP는 이 리프 범위 밖(L4-26 DoD (4)) — 여기 있는 함수 3개
(`get_order`/`list_orders`/`list_order_events`)만 배선한다. 전부 읽기
전용(SELECT만, UPDATE/INSERT 금지)이라 `order_repository.get_for_update`
(FOR UPDATE로 행을 잠그는 쓰기 경로 전용 메서드, L4-07)는 재사용하지
않는다 — 대신 이 파일이 직접 tenant 필터가 걸린 SELECT를 쥔다. 이벤트
이력만은 L4-07 `PostgresOrderEventRepository.timeline()`을 그대로
재사용한다(decision: "새 저장소 어댑터를 만들지 말 것" — 이미 있는
어댑터 메서드를 쓰는 것이므로 어긋나지 않는다).

tenant 격리(decision: LA-22/task-825 `BatchRepository.get()`과 동일
패턴): `orders.user_id`가 곧 tenant 식별자다(01번 스키마, `user_id NOT
NULL`이라 LA-22의 `IS NOT DISTINCT FROM`이 아니라 평범한 `=`로 충분 —
플랫폼 공용 행 개념이 orders엔 없다). 존재하지 않는 `order_id`와 다른
tenant 소유 `order_id`는 항상 같은 `None`으로 접는다(§8.3 "404 동형") —
호출부가 둘을 구분할 방법이 없다. PLT-30 RLS(orders는 아직
`_LEGACY_TABLES_POLICY_ONLY`)와는 이중 방어 — RLS가 나중에 켜져도
이 필터는 그대로 유효하고, RLS가 없는 지금은 이 필터가 유일한 방어선이다.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView

__all__ = [
    "MAX_PAGE_SIZE",
    "InvalidOrderCursorError",
    "get_order",
    "list_order_events",
    "list_orders",
]

MAX_PAGE_SIZE = 100
"""79번 §3 "maximum bounded page"와 동일 관례 — 호출자가 더 큰 값을
요청해도 이 값으로 잘라 무제한 조회를 막는다."""

_CURSOR_SEP = "|"


class InvalidOrderCursorError(Exception):
    """변조되었거나 형식이 깨진 `cursor`. fail-closed — 최선 추측으로
    페이지를 이어 붙이지 않고 즉시 거부한다(400 매핑은 호출부 책임)."""


def _row_to_order_view(row: asyncpg.Record) -> OrderView:
    return OrderView(
        order_id=row["order_id"],
        tenant_id=row["user_id"],
        execution_id=row["execution_id"],
        client_order_id=row["client_order_id"],
        exchange_order_id=row["exchange_order_id"],
        symbol=row["symbol"],
        venue_symbol=row["venue_symbol"],
        exchange=row["exchange"],
        side=OrderSide(row["side"]),
        order_type=OrderType(row["order_type"]),
        time_in_force=row["time_in_force"],
        quantity=row["quantity"],
        price=row["price"],
        status=OrderStatus(row["status"]),
        filled_quantity=row["filled_quantity"],
        average_fill_price=row["average_fill_price"],
        fee_total=row["fee_total"],
        fee_currency=row["fee_currency"],
        version=row["version"],
        parent_order_id=row["parent_order_id"],
        algo_run_id=row["algo_run_id"],
        unknown_since=row["unknown_since"],
        provider_order_date=row["provider_order_date"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _encode_cursor(created_at: datetime, order_id: UUID) -> str:
    raw = f"{created_at.isoformat()}{_CURSOR_SEP}{order_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at_str, _, order_id_str = raw.partition(_CURSOR_SEP)
        if not order_id_str:
            raise ValueError("cursor에 구분자가 없습니다")
        return datetime.fromisoformat(created_at_str), UUID(order_id_str)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidOrderCursorError(f"유효하지 않은 cursor: {cursor!r}") from exc


async def get_order(pool: asyncpg.Pool, order_id: UUID, *, tenant_id: UUID) -> OrderView | None:
    """`tenant_id` 소유가 아니면(미존재 포함) `None` — §8.3 404 동형."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_id = $1 AND user_id = $2", order_id, tenant_id
        )
    return None if row is None else _row_to_order_view(row)


async def list_orders(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    execution_id: int | None = None,
    statuses: Sequence[OrderStatus] | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[OrderView], str | None]:
    """tenant 소유 주문만, `created_at DESC, order_id DESC` keyset
    페이지(79번 §3 opaque cursor 관례 재사용). `statuses`로 미종결 상태
    집합을 넘기면 "미체결 목록"(§2-C `list_open`) 조회로도 쓸 수 있다."""
    bounded_limit = min(max(limit, 1), MAX_PAGE_SIZE)
    conditions = ["user_id = $1"]
    params: list[Any] = [tenant_id]
    if execution_id is not None:
        params.append(execution_id)
        conditions.append(f"execution_id = ${len(params)}")
    if statuses is not None:
        params.append([s.value for s in statuses])
        conditions.append(f"status = ANY(${len(params)})")
    if cursor is not None:
        cursor_created_at, cursor_order_id = _decode_cursor(cursor)
        params.extend([cursor_created_at, cursor_order_id])
        conditions.append(f"(created_at, order_id) < (${len(params) - 1}, ${len(params)})")
    where_clause = " AND ".join(conditions)
    params.append(bounded_limit + 1)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM orders WHERE {where_clause} "  # noqa: S608 — 식별자만 보간, 값은 파라미터
            f"ORDER BY created_at DESC, order_id DESC LIMIT ${len(params)}",
            *params,
        )

    has_more = len(rows) > bounded_limit
    page_rows = rows[:bounded_limit]
    items = [_row_to_order_view(row) for row in page_rows]
    next_cursor = (
        _encode_cursor(page_rows[-1]["created_at"], page_rows[-1]["order_id"])
        if has_more and page_rows
        else None
    )
    return items, next_cursor


async def list_order_events(
    pool: asyncpg.Pool, order_id: UUID, *, tenant_id: UUID
) -> list[OrderTransitionEvent] | None:
    """tenant 소유가 아니면(미존재 포함) `None`. 소유 확인 후에는 L4-07
    `PostgresOrderEventRepository.timeline()`을 그대로 재사용한다."""
    events_repo = PostgresOrderEventRepository()
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT 1 FROM orders WHERE order_id = $1 AND user_id = $2", order_id, tenant_id
        )
        if owner is None:
            return None
        return await events_repo.timeline(conn, order_id)
