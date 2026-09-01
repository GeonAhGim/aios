"""FD-4/FD-8 통합테스트 전용 거래소 어댑터 대역.

실제 Bitget API 호출 없이 place_order/get_order/cancel_order/modify_order
동작을 스크립트로 제어한다 — Demo API 키가 아직 없어(계정 확보는 별도
과제로 이연됨) 실거래소 왕복 자체는 검증할 수 없지만, ExchangeAdapter
인터페이스 계약을 지키는 한 이 대역으로 FD-4/FD-8 파이프라인 전체(멱등성,
DB 영속화, 이벤트 발행, FSM 전이)는 그대로 검증 가능하다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.data.models.base import AssetClass
from src.data.models.market_data import Candle, OrderBook, Ticker
from src.data.models.trading import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability, TickerCallback

PlaceOrderHook = Callable[[Order], Awaitable[Order]]


class FakeExchangeAdapter(ExchangeAdapter):
    def __init__(
        self,
        *,
        exchange_name: str = "bitget",
        is_paper_trading: bool = True,
        place_order_result_status: OrderStatus = OrderStatus.SUBMITTED,
        on_place_order: PlaceOrderHook | None = None,
        cancel_result: bool = True,
        get_order_status: OrderStatus = OrderStatus.FILLED,
        closes: list[Decimal] | None = None,
        usdt_balance: AccountBalance | None = None,
    ) -> None:
        self._exchange_name = exchange_name
        self._is_paper_trading = is_paper_trading
        self._place_order_result_status = place_order_result_status
        self._on_place_order = on_place_order
        self._cancel_result = cancel_result
        self._get_order_status = get_order_status
        # 실행 루프 오케스트레이터(tick.py) 테스트용 — 지표 계산에 쓸
        # 종가 시퀀스와 계좌 잔고를 스크립트로 고정한다.
        self._closes = closes or [Decimal("50000")] * 30
        self._usdt_balance = usdt_balance or AccountBalance(
            exchange=exchange_name, asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        )
        self.placed_orders: list[Order] = []
        self.place_order_call_count = 0

    @property
    def is_paper_trading(self) -> bool:
        return self._is_paper_trading

    def get_capabilities(self) -> ExchangeCapability:
        return ExchangeCapability(
            exchange_name=self._exchange_name,
            supported_asset_classes=[AssetClass.CRYPTO],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=False,
            max_leverage=Decimal("1"),
            reference_feed_coverage="high",
            has_official_sandbox=True,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        price = self._closes[-1]
        return Ticker(
            symbol=symbol,
            exchange=self._exchange_name,
            price=price,
            bid=price,
            ask=price,
            volume_24h=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        raise NotImplementedError

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        now = datetime.now(timezone.utc)
        return [
            Candle(
                symbol=symbol,
                exchange=self._exchange_name,
                timeframe=timeframe,
                open_time=now,
                close_time=now,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1"),
            )
            for close in self._closes[-limit:]
        ]

    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        raise NotImplementedError

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        return [self._usdt_balance]

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        return []

    async def get_order(self, order_id: str) -> Order:
        # Bitget.get_order()와 동일 원칙(자체 docstring 참조) — 거래소는
        # AIOS 전용 컨텍스트(strategy_id 등)를 모르니 자리표시자로 둔다.
        # FD-4.5/FD-3.4 호출부는 status/filled_quantity만 신뢰하면 된다.
        last = self.placed_orders[-1] if self.placed_orders else None
        return Order(
            order_id=uuid4(),
            exchange_order_id=order_id,
            client_order_id="",
            strategy_id="",
            strategy_version="",
            symbol=last.symbol if last is not None else "BTC/USDT",
            exchange=self._exchange_name,
            side=last.side if last is not None else OrderSide.BUY,
            order_type=last.order_type if last is not None else OrderType.MARKET,
            quantity=last.quantity if last is not None else Decimal("0"),
            status=self._get_order_status,
            filled_quantity=last.quantity if last is not None else Decimal("0"),
            asset_class=AssetClass.CRYPTO,
        )

    async def place_order(self, order: Order) -> Order:
        self.place_order_call_count += 1
        self.placed_orders.append(order)
        if self._on_place_order is not None:
            return await self._on_place_order(order)
        return order.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": self._place_order_result_status}
        )

    async def cancel_order(self, order_id: str) -> bool:
        return self._cancel_result

    async def modify_order(self, order_id: str, **kwargs: object) -> Order:
        return self.placed_orders[-1].model_copy(update={"exchange_order_id": order_id})

    async def health_check(self) -> bool:
        return True
