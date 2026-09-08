"""L4-21 — F5-b UNKNOWN reverse lookup: pure logic (response row parsing + candidate
matching, no I/O).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F5-b, §9 L4-21

KIS has no client_order_id concept (unlike F5-a, see the kis/adapter.py
module docstring), so exchange orders can't be reverse-looked-up by client
id — instead, candidates are matched by (symbol, side, quantity, price,
submit time ±30s) (§6 F5-b). If there are 0 candidates, the caller (the
future unknown_resolver F5-b path) proceeds with an ABSENT verdict; 1
candidate is adopted; **2 or more candidates trigger an immediate
ESCALATE** (`MultipleCandidateOrdersError`) rather than arbitrarily picking
one — the "no automatic judgment" principle.

`row_to_order` (maps the common row shape from inquire-psbl-rvsecncl/
inquire-daily-ccld to an Order) only matches naming conventions against
fields confirmed from the place_order response (KRX_FWDG_ORD_ORGNO/ODNO/
ORD_TMD) and has not been confirmed by a real-account round trip
(**unverified** — same principle as the other query methods, see
trading_query_mixin.py).
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
    """F5-b matching criteria — the original order's attributes as they remained in the
    local DB before UNKNOWN handling."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal | None
    submitted_at: datetime


class MultipleCandidateOrdersError(FatalExchangeError):
    """F5-b ESCALATE — automatic adoption isn't safe because there are 2 or more candidates.

    The caller must catch this exception and hand it off to an operator
    recovery_case (§6 F5-b) — there is deliberately no fallback that
    arbitrarily picks the first candidate (no automatic judgment).
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
    """Combines `ord_tmd`("HHMMSS") and `order_date`("YYYYMMDD") into a
    tz-aware UTC timestamp. If the field is missing or malformed, gives up
    on time matching and returns None instead — `_within_window` treats
    None conservatively as "time condition passed," so a missing timestamp
    never causes a candidate to be silently dropped."""
    if not ord_tmd or len(ord_tmd) != 6 or len(order_date) != 8:
        return None
    try:
        naive_kst = datetime.strptime(order_date + ord_tmd, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return (naive_kst - _KST_OFFSET).replace(tzinfo=timezone.utc)


def row_to_order(row: dict[str, Any], *, order_date: str) -> Order:
    """Maps the row shape common to inquire-psbl-rvsecncl (unfilled orders)
    and inquire-daily-ccld (today's fills) to an Order. Rows without
    `tot_ccld_qty` (the unfilled-orders list) are treated as having filled
    quantity 0."""
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
        client_order_id="",  # KIS has no client_order_id concept (see adapter docstring)
        strategy_id="",  # placeholder — the caller must fill this in via a DB lookup
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
    """§6 F5-b — 0 candidates → None (caller proceeds with an ABSENT
    verdict); 1 → adopted; ≥2 → `MultipleCandidateOrdersError` (ESCALATE, no
    automatic judgment)."""
    matched = [order for order in candidates if _matches(order, query)]
    if len(matched) >= 2:
        raise MultipleCandidateOrdersError(query, matched)
    return matched[0] if matched else None
