"""슬리피지·부분체결 모델(L4 명세 §2-F, R11).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F, §9 L4-22.

순수 도메인 — I/O 없음, 난수는 `rng` 인자로만 주입된다(전역 `random` 미사용).

가격 규칙(시뮬은 낙관 금지):
- 기준가: BUY=최우선 매도호가(ask), SELL=최우선 매수호가(bid).
- 슬리피지 bps = spread_bps/2 + impact_bps_per_pct_adv × (수량/ADV × 100).
  BUY는 기준가보다 **높게**, SELL은 **낮게** 체결된다(부호 불변).
- tick 라운딩은 `rounding.round_price`를 재사용하되 **반대 side**를 넘긴다 —
  `round_price`는 실주문에 유리한 방향(BUY 내림)이지만 시뮬 체결은 그
  반대(BUY 올림)여야 "시뮬이 실거래보다 좋게 나오는" 편향이 없다.
- LIMIT은 호가를 교차할 때만 체결되며(BUY: limit ≥ ask, SELL: limit ≤ bid)
  체결가는 limit보다 불리해지지 않는다. 미교차 = 빈 리스트(0체결).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from src.data.models.market_data import OrderBook
from src.data.models.trading import Order, OrderSide, OrderType
from src.services.oms.domain.rounding import round_price, round_qty

_BPS = Decimal("10000")
_HUNDRED = Decimal("100")


class RandomSource(Protocol):
    """`random.Random`과 호환되는 최소 계약 — 테스트는 고정값 대역을 넣는다."""

    def random(self) -> float: ...


@dataclass(frozen=True)
class SimFill:
    price: Decimal
    quantity: Decimal
    liquidity: Literal["MAKER", "TAKER"]
    slippage_bps: Decimal  # 부호 있음: BUY ≥ 0, SELL ≤ 0
    reference_price: Decimal


class EmptyBookError(ValueError):
    """MARKET 주문인데 반대편 호가가 비어 있음 — 무음 0체결 대신 명시적 실패."""


@dataclass(frozen=True)
class FillModel:
    spread_bps: Decimal
    impact_bps_per_pct_adv: Decimal
    partial_fill_prob: float
    partial_min_pct: Decimal

    def __post_init__(self) -> None:
        if self.spread_bps < 0 or self.impact_bps_per_pct_adv < 0:
            raise ValueError("spread_bps/impact_bps_per_pct_adv는 0 이상이어야 합니다.")
        if not 0.0 <= self.partial_fill_prob <= 1.0:
            raise ValueError("partial_fill_prob는 [0, 1] 범위여야 합니다.")
        if not 0 < self.partial_min_pct <= _HUNDRED:
            raise ValueError("partial_min_pct는 (0, 100] 범위여야 합니다.")

    def simulate(
        self,
        order: Order,
        book: OrderBook,
        adv: Decimal,
        rng: RandomSource,
        *,
        tick: Decimal = Decimal("0"),
        lot: Decimal = Decimal("0"),
    ) -> list[SimFill]:
        """한 번의 시뮬 체결 시도. 0개(미교차·잔량 없음·lot 미만) 또는 1개."""
        if adv <= 0:
            raise ValueError("adv(일평균거래량)는 양수여야 합니다.")
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return []

        reference = self._reference_price(order, book)
        if reference is None:
            return []
        if order.order_type == OrderType.LIMIT and not self._crosses(order, reference):
            return []

        qty = self._fill_quantity(remaining, rng, lot)
        if qty <= 0:
            return []

        slip = self.slippage_bps(qty, adv)
        signed_slip = slip if order.side == OrderSide.BUY else -slip
        raw_price = reference * (Decimal(1) + signed_slip / _BPS)
        price = self._bound_by_limit(order, raw_price)
        # 반대 side로 라운딩 → BUY는 올림, SELL은 내림(시뮬 낙관 금지).
        opposite = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
        price = round_price(price, tick, opposite)
        price = self._bound_by_limit(order, price)
        return [
            SimFill(
                price=price,
                quantity=qty,
                liquidity="TAKER",
                slippage_bps=signed_slip,
                reference_price=reference,
            )
        ]

    def slippage_bps(self, qty: Decimal, adv: Decimal) -> Decimal:
        """부호 없는 슬리피지 크기(bps). 참여율 = qty/adv × 100(%)."""
        participation_pct = qty / adv * _HUNDRED
        return self.spread_bps / 2 + self.impact_bps_per_pct_adv * participation_pct

    @staticmethod
    def _reference_price(order: Order, book: OrderBook) -> Decimal | None:
        levels = book.asks if order.side == OrderSide.BUY else book.bids
        if not levels:
            if order.order_type == OrderType.MARKET:
                raise EmptyBookError(f"{book.symbol}: {order.side.value} 반대편 호가가 비어 있음")
            return None
        return levels[0].price

    @staticmethod
    def _crosses(order: Order, reference: Decimal) -> bool:
        if order.price is None:
            raise ValueError("LIMIT 주문에 price가 없습니다.")
        limit = order.price.amount
        return limit >= reference if order.side == OrderSide.BUY else limit <= reference

    @staticmethod
    def _bound_by_limit(order: Order, price: Decimal) -> Decimal:
        if order.order_type != OrderType.LIMIT or order.price is None:
            return price
        limit = order.price.amount
        return min(price, limit) if order.side == OrderSide.BUY else max(price, limit)

    def _fill_quantity(self, remaining: Decimal, rng: RandomSource, lot: Decimal) -> Decimal:
        """부분체결 판정. prob=0이면 rng를 소비하지 않고 전량, prob=1이면 항상
        [min_pct, 100)% 구간(min_pct=100이면 전량과 동일 — 경계)."""
        if self.partial_fill_prob > 0 and rng.random() < self.partial_fill_prob:
            span = _HUNDRED - self.partial_min_pct
            fraction = (self.partial_min_pct + span * Decimal(str(rng.random()))) / _HUNDRED
            return round_qty(remaining * fraction, lot)
        return round_qty(remaining, lot)
