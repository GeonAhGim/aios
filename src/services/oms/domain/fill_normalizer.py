"""거래소 원시 체결 → `FillEvent`, 누적 집계(L4 명세 §2-A, R7).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A, §9 L4-05.

R7 — "체결 정규화 정밀도" — Decimal, 부분체결 누적 평균가·수수료.

`normalize_fill()`의 `raw`는 venue별 원시 응답 그 자체가 아니다 — 그건
`exchanges/{bitget,kis,nh}/*_mixin.py`(다른 세션 소유, 아직 없음)의 책임이다.
이 domain 함수는 그 믹스인이 만들어 넘겨줄 **venue-중립 최소 키 집합**만
계약으로 받는다(아래 필수 키 목록) — I/O도, venue별 분기도 없다.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from src.data.models.trading import OrderSide
from src.services.oms.contracts.v1_events import FillAggregate, FillEvent
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import VenueCapabilityProfile

_REQUIRED_RAW_KEYS = (
    "fill_id",
    "exchange_order_id",
    "venue_symbol",
    "side",
    "quantity",
    "price",
    "fee",
    "fee_currency",
    "venue_ts",
)


def normalize_fill(
    raw: dict[str, Any],
    *,
    venue: str,
    profile: VenueCapabilityProfile,
    registry: SymbolRegistry,
) -> FillEvent:
    """`raw` 필수 키: fill_id, exchange_order_id, venue_symbol, side
    ("BUY"/"SELL" 또는 OrderSide), quantity, price, fee(Decimal 변환
    가능한 값), fee_currency, venue_ts(datetime). 선택: order_id(내부
    UUID를 이미 아는 경우), liquidity("MAKER"/"TAKER", 기본 "UNKNOWN")."""
    missing = [k for k in _REQUIRED_RAW_KEYS if k not in raw]
    if missing:
        raise ValueError(f"normalize_fill: raw에 필수 키가 없습니다: {missing}")
    if venue != profile.venue:
        raise ValueError(f"venue({venue})와 profile.venue({profile.venue})가 다릅니다.")

    side_raw = raw["side"]
    side = side_raw if isinstance(side_raw, OrderSide) else OrderSide(str(side_raw).upper())
    canonical_symbol = registry.to_canonical(str(raw["venue_symbol"]), venue)

    liquidity_raw = raw.get("liquidity", "UNKNOWN")
    liquidity: Literal["MAKER", "TAKER", "UNKNOWN"] = (
        liquidity_raw if liquidity_raw in ("MAKER", "TAKER", "UNKNOWN") else "UNKNOWN"
    )

    order_id_raw = raw.get("order_id")
    order_id = UUID(str(order_id_raw)) if order_id_raw is not None else None

    venue_ts = raw["venue_ts"]
    if not isinstance(venue_ts, datetime):
        raise ValueError("normalize_fill: venue_ts는 datetime이어야 합니다.")

    return FillEvent(
        provider_fill_id=str(raw["fill_id"]),
        venue=venue,
        order_id=order_id,
        exchange_order_id=str(raw["exchange_order_id"]),
        symbol=canonical_symbol,
        side=side,
        quantity=Decimal(str(raw["quantity"])),
        price=Decimal(str(raw["price"])),
        fee=Decimal(str(raw["fee"])),
        fee_currency=str(raw["fee_currency"]),
        liquidity=liquidity,
        venue_ts=venue_ts,
    )


def aggregate(fills: Sequence[FillEvent]) -> FillAggregate:
    """Σ(p·q)/Σq 평균가(Decimal 정밀도 그대로 — 표시 자릿수 반올림은
    호출부가 `rounding.round_price()`로 필요할 때만 한다), 통화별 수수료
    합계."""
    if not fills:
        return FillAggregate(filled_qty=Decimal("0"), avg_price=Decimal("0"), fee_total={})

    total_qty = Decimal("0")
    weighted_price_sum = Decimal("0")
    fee_total: dict[str, Decimal] = {}

    for fill in fills:
        total_qty += fill.quantity
        weighted_price_sum += fill.price * fill.quantity
        fee_total[fill.fee_currency] = fee_total.get(fill.fee_currency, Decimal("0")) + fill.fee

    avg_price = weighted_price_sum / total_qty if total_qty != 0 else Decimal("0")
    return FillAggregate(filled_qty=total_qty, avg_price=avg_price, fee_total=fee_total)
