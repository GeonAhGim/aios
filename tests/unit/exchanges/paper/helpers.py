"""L4-22 paper 모델 테스트 공용 빌더 — DB 없음, 전역 난수 없음."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.market_data import OrderBook, OrderBookLevel
from src.data.models.trading import Order, OrderSide, OrderType
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile


class FixedRng:
    """주입 전용 난수 대역 — 미리 정한 값을 순서대로 돌려주고 소비 횟수를 센다."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = list(values)
        self.calls = 0

    def random(self) -> float:
        if self.calls >= len(self._values):
            raise AssertionError("FixedRng: 예상보다 많은 rng 호출")
        value = self._values[self.calls]
        self.calls += 1
        return value


def make_order(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: str = "1",
    limit: str | None = None,
    filled: str = "0",
) -> Order:
    price = Money(amount=Decimal(limit), currency=Currency.USDT) if limit is not None else None
    return Order(
        client_order_id="coid-1",
        strategy_id="s1",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        price=price,
        filled_quantity=Decimal(filled),
        asset_class=AssetClass.CRYPTO,
    )


def make_book(bid: str | None = "100", ask: str | None = "101") -> OrderBook:
    bids = [OrderBookLevel(price=Decimal(bid), quantity=Decimal("10"))] if bid else []
    asks = [OrderBookLevel(price=Decimal(ask), quantity=Decimal("10"))] if ask else []
    return OrderBook(
        symbol="BTC/USDT",
        exchange="paper_sim",
        bids=bids,
        asks=asks,
        timestamp=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )


def make_profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {"BTC/USDT": Decimal("0.1")},
        "qty_lot": {"BTC/USDT": Decimal("0.0001")},
        "min_notional": {"BTC/USDT": Decimal("5")},
        "rate_limits": {"order": (10, 20)},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "LIVE_VERIFIED",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile(**defaults)  # type: ignore[arg-type]
