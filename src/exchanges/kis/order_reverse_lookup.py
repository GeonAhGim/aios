"""L4-21 — F5-b UNKNOWN 역조회: 순수 로직(응답 행 파싱 + 후보 매칭, I/O 없음).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F5-b, §9 L4-21

KIS는 client_order_id 개념이 없어(F5-a와 달리, kis/adapter.py 모듈
docstring 참조) client id로 거래소 주문을 역조회할 수 없다 — 대신
(symbol, side, quantity, price, 제출시각±30s)로 후보를 매칭한다(§6 F5-b).
후보가 0개면 호출부(향후 unknown_resolver F5-b 경로)가 ABSENT 판정을
이어가고, 1개면 채택, **2개 이상이면 임의로 하나를 고르지 않고 즉시
ESCALATE**(`MultipleCandidateOrdersError`) — "자동 판단 금지" 원칙.

`row_to_order`(inquire-psbl-rvsecncl/inquire-daily-ccld 공통 행 형태 →
Order)는 place_order 응답으로 확인된 필드(KRX_FWDG_ORD_ORGNO/ODNO/
ORD_TMD)와 이름 관례를 맞췄을 뿐 실계좌 왕복으로 확인하지 못했다
(**미검증** — 다른 조회 메서드와 동일 원칙, trading_query_mixin.py 참조).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType

MATCH_WINDOW_SECONDS = 30.0
_KST_OFFSET = timedelta(hours=9)


@dataclass(frozen=True)
class OrderMatchQuery:
    """F5-b 매칭 기준 — UNKNOWN 처리 전 로컬 DB에 남아있던 원주문 속성."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal | None
    submitted_at: datetime


class MultipleCandidateOrdersError(FatalExchangeError):
    """F5-b ESCALATE — 후보가 2개 이상이라 자동 채택이 안전하지 않다.

    호출부는 이 예외를 잡아 운영자 recovery_case로 넘겨야 한다(§6 F5-b) —
    첫 번째 후보를 임의로 고르는 폴백은 만들지 않는다(자동 판단 금지).
    """

    def __init__(self, query: OrderMatchQuery, candidates: list[Order]) -> None:
        self.query = query
        self.candidates = candidates
        ids = [c.exchange_order_id for c in candidates]
        super().__init__(
            f"F5-b 역조회 후보 {len(candidates)}개 — 자동 판단 불가(ESCALATE): "
            f"symbol={query.symbol} side={query.side.value} qty={query.quantity} "
            f"candidates={ids}"
        )


def _parse_ord_tmd(order_date: str, ord_tmd: str | None) -> datetime | None:
    """`ord_tmd`("HHMMSS")와 `order_date`("YYYYMMDD")를 합쳐 tz-aware UTC
    시각을 만든다. 필드가 없거나 형식이 어긋나면 시각 매칭을 포기하고
    None을 돌려준다 — `_within_window`가 None을 "시각 조건 통과"로 보수적
    으로 처리해, 시각 정보 부재가 조용한 후보 누락으로 이어지지 않는다."""
    if not ord_tmd or len(ord_tmd) != 6 or len(order_date) != 8:
        return None
    try:
        naive_kst = datetime.strptime(order_date + ord_tmd, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return (naive_kst - _KST_OFFSET).replace(tzinfo=timezone.utc)


def row_to_order(row: dict[str, Any], *, order_date: str) -> Order:
    """inquire-psbl-rvsecncl(미체결)/inquire-daily-ccld(당일 체결) 공통
    행 형태를 Order로 매핑한다. `tot_ccld_qty`가 없는 행(미체결 목록)은
    체결수량 0으로 본다."""
    ord_qty = Decimal(row.get("ord_qty", "0"))
    filled_quantity = Decimal(row.get("tot_ccld_qty", "0"))
    if filled_quantity == 0:
        status = OrderStatus.ACKNOWLEDGED
    elif filled_quantity < ord_qty:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.FILLED

    price_raw = row.get("ord_unpr") or "0"
    price = (
        Money(amount=Decimal(price_raw), currency=Currency.KRW)
        if Decimal(price_raw) != 0
        else None
    )
    created_at = _parse_ord_tmd(order_date, row.get("ord_tmd")) or datetime.now(timezone.utc)
    return Order(
        order_id=uuid4(),
        exchange_order_id=f"{row.get('krx_fwdg_ord_orgno', '')}:{row.get('odno', '')}",
        client_order_id="",  # KIS는 client_order_id 개념이 없음(어댑터 docstring 참조)
        strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
        strategy_version="",
        symbol=row.get("pdno", ""),
        exchange="kis",
        side=OrderSide.BUY if row.get("sll_buy_dvsn_cd") == "02" else OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=ord_qty,
        price=price,
        status=status,
        filled_quantity=filled_quantity,
        created_at=created_at,
        updated_at=created_at,
        asset_class=AssetClass.KR_EQUITY,
    )


def _within_window(order: Order, query: OrderMatchQuery) -> bool:
    if order.created_at is None:
        return True
    delta = abs((order.created_at - query.submitted_at).total_seconds())
    return delta <= MATCH_WINDOW_SECONDS


def _matches(order: Order, query: OrderMatchQuery) -> bool:
    if order.symbol != query.symbol or order.side is not query.side:
        return False
    if order.quantity != query.quantity:
        return False
    if query.price is not None:
        order_amount = order.price.amount if order.price is not None else None
        if order_amount != query.price:
            return False
    return _within_window(order, query)


def find_matching_order(query: OrderMatchQuery, candidates: list[Order]) -> Order | None:
    """§6 F5-b — 후보 0개 → None(호출부가 ABSENT 판정을 이어감), 1개 →
    채택, ≥2개 → `MultipleCandidateOrdersError`(ESCALATE, 자동 판단 금지)."""
    matched = [order for order in candidates if _matches(order, query)]
    if len(matched) >= 2:
        raise MultipleCandidateOrdersError(query, matched)
    return matched[0] if matched else None
