"""`PaperSimulatorAdapter` 테스트 전용 대역(L4-23, task-1605).

`FakeExchangeAdapter`(FD-4/FD-8 전용, `get_orderbook`은 NotImplementedError)를
확장해 고정 호가만 채운다 — paper 시뮬레이터는 시세를 `reference`에서
빌려 쓰므로(모듈 docstring) 테스트가 결정론적 체결가를 얻으려면 고정
orderbook이 필요하다. `SeqRandom`은 `fill_model`/`latency_model`이 요구하는
`RandomSource` 계약(`random() -> float`)의 스크립트형 대역이다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.market_data import OrderBook, OrderBookLevel
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


class FakeReferenceAdapter(FakeExchangeAdapter):
    def __init__(
        self, *, bid: Decimal, ask: Decimal, depth_qty: Decimal = Decimal("100"), **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._bid = bid
        self._ask = ask
        self._depth_qty = depth_qty

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:  # noqa: ARG002
        return OrderBook(
            symbol=symbol,
            exchange=self._exchange_name,
            bids=[OrderBookLevel(price=self._bid, quantity=self._depth_qty)],
            asks=[OrderBookLevel(price=self._ask, quantity=self._depth_qty)],
            timestamp=datetime.now(timezone.utc),
        )


class SeqRandom:
    """고정 시퀀스를 순환 반환 — `[0.0]`이면 항상 최소분위(지연 0, 부분체결 없음)."""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._i = 0

    def random(self) -> float:
        value = self._values[self._i % len(self._values)]
        self._i += 1
        return value


async def instant_sleep(_seconds: float) -> None:
    return None


async def fixed_adv(_symbol: str) -> Decimal:
    return Decimal("1000000")
