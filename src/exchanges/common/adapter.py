"""6.2 — ExchangeAdapter 추상 클래스.

Spec: 02_exchange_adapter_v1.2.md#§2.1

7.4 — 상위 계층(Strategy/Portfolio/Risk)은 이 인터페이스만 알면 되고,
거래소별 API 차이는 이 클래스의 구현체 내부로 격리된다.

7.9 원칙 — 이 클래스의 어떤 메서드도 출금(withdraw) 기능을 포함하지 않는다.
Trading Permission ≠ Withdrawal Permission.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.data.models.market_data import Candle, OrderBook, Ticker
from src.data.models.trading import AccountBalance, Order, Position
from src.exchanges.common.types import ExchangeCapability, TickerCallback


class ExchangeAdapter(ABC):
    @abstractmethod
    def get_capabilities(self) -> ExchangeCapability:
        """7.6 — 이 거래소가 지원하는 기능을 선언한다."""
        ...

    @property
    @abstractmethod
    def is_sandboxed(self) -> bool:
        """레드팀 감사(docs/RED_TEAM_FINDINGS.md, 2026-09-01-08) 반영 — 이
        어댑터 인스턴스가 실제로 sandbox/demo/paper 계정에 바인딩됐는지
        스스로 증명한다. DB의 실행 mode 컬럼만으로는 "잘못 구성된 real
        adapter"(예: demo_mode=False로 생성된 BitgetAdapter)를 걸러낼 수
        없다 — FD-8.4(Executor)가 PAPER 모드 실행에 이 값을 반드시 함께
        확인해야 한다(mode 문자열과 실제 adapter 상태 둘 다 일치해야
        통과, 어느 한쪽만 봐서는 안 됨)."""
        ...

    # ---- Market Data ----
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook: ...

    @abstractmethod
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]: ...

    @abstractmethod
    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        """WebSocket 실시간 구독. supports_websocket=False인 거래소는
        NotImplementedError를 발생시키고 REST 폴링으로의 폴백은 호출부 책임.

        재연결 책임 — 연결 끊김 시 재연결·재구독은 이 Adapter 구현체
        내부 책임이다. 재연결 중에는 05번 문서의 'market.distrust.entered'류
        이벤트를 발행해 상위 계층에 알려야 한다(8.1-A와 동일 원칙)."""
        ...

    # ---- Account ----
    @abstractmethod
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...

    @abstractmethod
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order:
        """7.5 주문 멱등성 — UNKNOWN 상태 재확인 시 반드시 이 메서드로
        거래소 실제 상태를 재조회한다."""
        ...

    # ---- Trading (FROZEN 인접 — Executor를 통해서만 호출됨) ----
    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        """이 메서드는 스스로 Risk Check를 수행하지 않는다 — 8.2-A Master
        Authority에 따라 Risk Check는 이 메서드 호출 이전에 완료돼 있어야 한다."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def modify_order(self, order_id: str, **kwargs: Any) -> Order: ...

    # ---- Health Check (8.6-A-1-2 Split-Brain 방어) ----
    @abstractmethod
    async def health_check(self) -> bool:
        """Watchdog이 State DB와 무관하게 독립적으로 호출하는 거래소 자체
        응답성 확인용 메서드. 잔고 조회 등 경량 호출로 구현한다."""
        ...
