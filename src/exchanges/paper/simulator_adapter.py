"""PAPER 시뮬레이터 `ExchangeAdapter` 구현체(L4 명세 §2-F, §9 L4-23).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F
`simulator_adapter.py` 행("시세는 read-only 실어댑터에서, 체결은 모델로"),
§6 F17(DROP → UNKNOWN 경로).

체결/수수료/지연은 전부 L4-22 순수 모델(`fill_model`/`fee_model`/
`latency_model`)에 위임한다 — 이 파일은 그 결과를 원장(`ledger_repository`,
L4-23)에 적용하고 `ExchangeAdapter` 계약으로 감싸는 오케스트레이션만 한다
(재구현 금지, task-1605 note DoD 1).

DROP 의미론(`latency_model` 모듈 docstring 그대로) — 요청은 나갔고 응답만
유실이다. 그래서 `place_order`는 latency 판정과 무관하게 원장 갱신을
**항상 커밋**한 뒤, `DROP`일 때만 `SentUnknownError`를 던져 호출자에게
"venue 진실은 있지만 이 응답은 못 받았다"를 알린다 — 이후
`find_order_by_client_id`/`get_order`(둘 다 원장을 그대로 재조회)가
`unknown_resolver`(L4-16, 아직 없음)의 해소 조회를 받아낼 수 있다.

잔고 이동은 사전 예약(margin hold) 없이 실제 체결 시점에만 일어난다 —
지정가 미체결(0건 체결) 주문은 원장에 ACKNOWLEDGED로만 남고 잔고는
안 움직인다. 이 편차(미검증) 때문에 동시에 여러 미체결 주문이 같은
잔고를 초과 배분할 수 있다는 한계가 있다 — 사전 예약은 L4-30+ 범위.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import asyncpg

from src.core.exceptions import MihwaError
from src.data.models.base import AssetClass, Currency, Money
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
from src.exchanges.common.live_guard import require_paper_sandbox
from src.exchanges.common.types import ExchangeCapability, TickerCallback
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import FillModel, RandomSource, SimFill
from src.exchanges.paper.latency_model import LatencyModel, Sleeper
from src.exchanges.paper.ledger_repository import PaperLedgerRepository, PaperOrderRow
from src.exchanges.paper.venue_profile import profile_for

if TYPE_CHECKING:
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile

AdvProvider = Callable[[str], Awaitable[Decimal]]


class SentUnknownError(MihwaError):
    """§6 F17 — 요청은 venue에 반영됐지만 이 응답은 유실됐다는 신호.

    이 예외를 삼키는 호출부는 사고다(모듈 docstring). `order_id`는
    venue측(`paper_sim_orders.order_id`) — OMS 내부 id가 아니다."""

    def __init__(self, client_order_id: str, order_id: UUID) -> None:
        super().__init__(
            f"client_order_id={client_order_id}: 응답 유실(DROP) — venue측 order_id="
            f"{order_id}는 실제로 처리됐다(find_order_by_client_id로 해소 가능)."
        )
        self.client_order_id = client_order_id
        self.order_id = order_id


class PaperSimulatorAdapter(ExchangeAdapter):
    """`reference`(실 거래소 read-only 어댑터)에서 시세만 빌리고, 주문·체결·
    잔고는 전부 이 클래스와 `PaperLedgerRepository`가 자체 관리한다."""

    def __init__(
        self,
        reference: ExchangeAdapter,
        ledger: PaperLedgerRepository,
        fill_model: FillModel,
        fee_model: FeeModel,
        latency_model: LatencyModel,
        clock: Callable[[], datetime],
        rng: RandomSource,
        *,
        account_id: UUID,
        pool: asyncpg.Pool,
        adv_provider: AdvProvider,
        sleeper: Sleeper,
    ) -> None:
        self._reference = reference
        self._ledger = ledger
        self._fill = fill_model
        self._fee = fee_model
        self._latency = latency_model
        self._clock = clock
        self._rng = rng
        self._account_id = account_id
        self._pool = pool
        self._adv_provider = adv_provider
        self._sleeper = sleeper

    @property
    def is_paper_trading(self) -> bool:
        return True

    @property
    def is_sandboxed(self) -> bool:
        return True

    def get_capabilities(self) -> ExchangeCapability:
        return self._reference.get_capabilities()

    async def get_ticker(self, symbol: str) -> Ticker:
        return await self._reference.get_ticker(symbol)

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        return await self._reference.get_orderbook(symbol, depth)

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        return await self._reference.get_ohlcv(symbol, timeframe, limit)

    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        await self._reference.subscribe_ticker_stream(symbol, callback)

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        async with self._pool.acquire() as conn:
            return await self._ledger.list_balances(conn, self._account_id, asset)

    async def get_positions(self, symbol: str | None = None) -> list[Position]:  # noqa: ARG002
        """미검증/범위 밖 — 이 리프는 스팟 잔고만 다룬다(포지션 산출 없음)."""
        return []

    async def get_order(self, order_id: str) -> Order:
        async with self._pool.acquire() as conn:
            row = await self._ledger.get_order(conn, self._account_id, UUID(order_id))
        return self._row_to_order(row)

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        async with self._pool.acquire() as conn:
            row = await self._ledger.find_by_client_id(conn, self._account_id, client_order_id)
        return None if row is None else self._row_to_order(row)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        async with self._pool.acquire() as conn:
            rows = await self._ledger.list_orders(conn, self._account_id, symbol)
        return [self._row_to_order(row) for row in rows]

    async def get_fills(
        self,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, object]]:
        if since is not None and since.tzinfo is None:
            raise ValueError("get_fills: since는 tz-aware UTC여야 합니다(naive 거부).")
        async with self._pool.acquire() as conn:
            if order_id is not None:
                rows = [await self._ledger.get_order(conn, self._account_id, UUID(order_id))]
            else:
                rows = await self._ledger.list_orders(
                    conn, self._account_id, symbol, open_only=False
                )
        raw: list[dict[str, object]] = []
        for row in rows:
            if symbol is not None and row.symbol != symbol:
                continue
            for entry in row.fills:
                venue_ts = datetime.fromisoformat(str(entry["venue_ts"]))
                if since is not None and venue_ts <= since:
                    continue
                raw.append(
                    {
                        "fill_id": entry["fill_id"],
                        "exchange_order_id": str(row.order_id),
                        "venue_symbol": row.symbol,
                        "side": row.side,
                        "quantity": Decimal(str(entry["quantity"])),
                        "price": Decimal(str(entry["price"])),
                        "fee": Decimal(str(entry["fee"])),
                        "fee_currency": entry["fee_currency"],
                        "venue_ts": venue_ts,
                        "liquidity": entry["liquidity"],
                    }
                )
        return raw

    def venue_profile(self) -> VenueCapabilityProfile:
        return profile_for(self._reference.venue_profile())

    @require_paper_sandbox
    async def place_order(self, order: Order) -> Order:
        async with self._pool.acquire() as conn:
            existing = await self._ledger.find_by_client_id(
                conn, self._account_id, order.client_order_id
            )
            if existing is not None:
                return self._row_to_order(existing)  # DoD 5 — 재전송은 원장 1행, 재시뮬 없음

            book = await self._reference.get_orderbook(order.symbol)
            adv = await self._adv_provider(order.symbol)
            outcome = await self._latency.apply(self._rng, sleeper=self._sleeper)
            sim_fills = self._fill.simulate(order, book, adv, self._rng)

            # insert+정산을 한 트랜잭션으로 묶는다 — 잔고 부족으로 settle이
            # 실패하면 방금 만든 주문 행까지 통째로 롤백돼야 fail-closed다
            # (부분 상태로 남은 ACKNOWLEDGED 유령 주문을 만들지 않는다).
            async with conn.transaction():
                row, created = await self._ledger.insert_order(
                    conn,
                    account_id=self._account_id,
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=order.order_type.value,
                    quantity=order.quantity,
                    price=order.price.amount if order.price is not None else None,
                )
                if created:
                    for sim_fill in sim_fills:
                        row = await self._settle_fill(conn, row, order, sim_fill)

        if outcome.kind == "DROP":
            raise SentUnknownError(order.client_order_id, row.order_id)
        return self._row_to_order(row)

    async def _settle_fill(
        self, conn: asyncpg.Connection, row: PaperOrderRow, order: Order, sim_fill: SimFill
    ) -> PaperOrderRow:
        fee = self._fee.fee(sim_fill)
        base_asset, quote_asset = order.symbol.split("/")
        notional = sim_fill.price * sim_fill.quantity
        if order.side == OrderSide.BUY:
            await self._ledger.debit(conn, self._account_id, quote_asset, notional)
            await self._ledger.deposit(conn, self._account_id, base_asset, sim_fill.quantity)
        else:
            await self._ledger.debit(conn, self._account_id, base_asset, sim_fill.quantity)
            await self._ledger.deposit(conn, self._account_id, quote_asset, notional)
        await self._ledger.debit(conn, self._account_id, fee.currency.value, fee.amount)
        return await self._ledger.apply_fill(
            conn,
            account_id=self._account_id,
            order_id=row.order_id,
            expected_version=row.version,
            fill_price=sim_fill.price,
            fill_quantity=sim_fill.quantity,
            liquidity=sim_fill.liquidity,
            fee_amount=fee.amount,
            fee_currency=fee.currency.value,
            venue_ts=self._clock(),
        )

    @require_paper_sandbox
    async def cancel_order(self, order_id: str) -> bool:
        async with self._pool.acquire() as conn:
            return await self._ledger.cancel(conn, self._account_id, UUID(order_id))

    @require_paper_sandbox
    async def modify_order(self, order_id: str, **kwargs: object) -> Order:  # noqa: ARG002
        """범위 밖(L4-23 DoD 미포함) — 정정은 명시적으로 미지원."""
        raise self._unsupported("modify_order")

    async def health_check(self) -> bool:
        return await self._reference.health_check()

    def _row_to_order(self, row: PaperOrderRow) -> Order:
        quote_asset = row.symbol.split("/")[1]
        price = Money(amount=row.price, currency=Currency(quote_asset)) if row.price else None
        avg_price = (
            Money(amount=row.average_fill_price, currency=Currency(quote_asset))
            if row.average_fill_price is not None
            else None
        )
        return Order(
            order_id=uuid4(),  # venue는 OMS 내부 id를 모른다(FakeExchangeAdapter와 동일 관례)
            exchange_order_id=str(row.order_id),
            client_order_id=row.client_order_id,
            strategy_id="",
            strategy_version="",
            symbol=row.symbol,
            exchange="paper_sim",
            side=OrderSide(row.side),
            order_type=OrderType(row.order_type),
            quantity=row.quantity,
            price=price,
            status=OrderStatus(row.status),
            filled_quantity=row.filled_quantity,
            average_fill_price=avg_price,
            created_at=row.created_at,
            updated_at=row.updated_at,
            asset_class=AssetClass.CRYPTO,
        )
