"""6.2 — ExchangeAdapter 추상 클래스.

Spec: 02_exchange_adapter_v1.2.md#§2.1,
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B(adapter.py 행), §9 L4-13

7.4 — 상위 계층(Strategy/Portfolio/Risk)은 이 인터페이스만 알면 되고,
거래소별 API 차이는 이 클래스의 구현체 내부로 격리된다.

7.9 원칙 — 이 클래스의 어떤 메서드도 출금(withdraw) 기능을 포함하지 않는다.
Trading Permission ≠ Withdrawal Permission.

L4-13(task-1519) — OMS 워커(L4-14 outbox, L4-16 UNKNOWN resolver, L4-24
3자 대사)가 소비할 조회 계약 5종을 ABC에 **하위호환 기본 구현**으로
추가한다: `get_open_orders`, `get_fills(since=)`, `find_order_by_client_id`,
`venue_profile`, `subscribe_order_stream`. 추상 메서드로 추가하면 기존
구현체(KIS/NH, 테스트 대역 `FakeExchangeAdapter` 등)가 전부 인스턴스화
불가가 되므로 기본 구현을 둔다.

기본 구현은 **무음 빈 결과를 돌려주지 않는다** — `[]`/`None`을 돌려주면
UNKNOWN resolver가 "거래소에 주문이 없다"로 오해해 `RESOLVED_ABSENT`
(주문 FAILED 확정)로 흘러가는 사고가 난다(§6 F5-a). 대신 명시적
`UnsupportedCapabilityError`를 던진다. `NotImplementedError`의 서브클래스로
만들지 않는 이유: 호출부의 `except NotImplementedError` 폴백 경로
(예: `subscribe_ticker_stream` 미지원 → REST 폴링)와 섞이면 "미구현"과
"이 venue는 그 capability가 없음"이 구분되지 않는다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.core.exceptions import MihwaError
from src.data.models.market_data import Candle, OrderBook, Ticker
from src.data.models.trading import AccountBalance, Order, Position
from src.exchanges.common.types import ExchangeCapability, TickerCallback

if TYPE_CHECKING:
    # 반환 타입 주석에만 쓴다 — exchanges 계층이 services.oms 도메인 모듈을
    # 런타임 import하지 않도록(계층 의존 방향 유지) TYPE_CHECKING으로 가둔다.
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile

OrderCallback = Callable[[Order], Awaitable[None]]


class UnsupportedCapabilityError(MihwaError):
    """어댑터가 해당 capability를 제공하지 않는다는 **명시적** 신호.

    `capability`는 ABC 메서드 이름, `adapter`는 구현 클래스 이름. 호출부는
    이 예외를 "결과 없음"으로 해석해서는 안 되며, venue 프로파일
    (`supports_client_order_id`/`supports_ws_orders`)로 사전 분기해야 한다.
    """

    def __init__(self, capability: str, adapter: str) -> None:
        super().__init__(
            f"{adapter}은(는) `{capability}` capability를 제공하지 않습니다 — "
            "빈 결과로 대체하지 않고 호출부가 venue 프로파일로 분기해야 합니다."
        )
        self.capability = capability
        self.adapter = adapter


class ExchangeAdapter(ABC):
    @property
    @abstractmethod
    def is_paper_trading(self) -> bool:
        """Whether this adapter is configured for a provider sandbox account.

        This is an execution-boundary assertion, not a UI label. PAPER
        executors fail closed unless it is true; production egress policy and
        credential provenance remain independent deployment controls.
        """
        ...

    @property
    @abstractmethod
    def is_sandboxed(self) -> bool:
        """레드팀 감사(2026-09-01-08) — adapter 스스로가 sandbox/demo 계정에
        바인딩돼 있음을 증명하는 두 번째 독립 신호. `is_paper_trading`과
        같은 값을 반환하는 구현이 많지만(현재 모든 콘크리트 어댑터가 그럼),
        DB의 mode 문자열 하나만으로는 잘못 구성된 실계정 adapter를 막지
        못하므로 Executor는 이 값도 별도로 확인한다.
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> ExchangeCapability:
        """7.6 — 이 거래소가 지원하는 기능을 선언한다."""
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

    # ---- L4-13 조회 계약(기본 구현 = 명시적 미지원, 모듈 docstring 참조) ----
    def _unsupported(self, capability: str) -> UnsupportedCapabilityError:
        return UnsupportedCapabilityError(capability, type(self).__name__)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:  # noqa: ARG002
        """미체결 주문 목록(§6 F5-b 역조회, L4-24 대사). 미지원 venue는
        빈 목록이 아니라 예외 — 빈 목록은 "미체결 없음"이라는 사실 주장이다."""
        raise self._unsupported("get_open_orders")

    async def get_fills(
        self,
        symbol: str | None = None,  # noqa: ARG002
        *,
        order_id: str | None = None,  # noqa: ARG002
        since: datetime | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """원시 체결 행(`fill_normalizer.normalize_fill` 입력). `since`는
        tz-aware UTC여야 하며 naive datetime은 구현체가 거부한다(fail-closed)."""
        raise self._unsupported("get_fills")

    async def find_order_by_client_id(
        self, client_order_id: str  # noqa: ARG002
    ) -> Order | None:
        """client id 역조회(§6 F5-a, F14 DUPLICATE_CLIENT_ID 채택). 반환
        `None`은 "거래소가 그 id를 모른다"는 **사실**이므로, client id를
        지원하지 않는 venue(`supports_client_order_id=False`)는 None을
        돌려주면 안 되고 이 기본 구현처럼 예외여야 한다."""
        raise self._unsupported("find_order_by_client_id")

    def venue_profile(self) -> VenueCapabilityProfile:
        """§3.2 capability 프로파일. 거래소별 상수(`exchanges/<venue>/
        venue_profile.py`, L4-04 잔여분)가 붙기 전까지는 기본 구현이 예외 —
        프로파일 없이 `assert_supported`를 건너뛰는 경로를 만들지 않는다."""
        raise self._unsupported("venue_profile")

    async def subscribe_order_stream(self, callback: OrderCallback) -> None:  # noqa: ARG002
        """private 주문 이벤트 스트림(L4-20 inbox 공급원). `supports_ws_orders
        =False`인 venue는 REST 폴링(`get_open_orders`)으로 대체하되, 그 결정은
        호출부가 프로파일을 보고 내린다."""
        raise self._unsupported("subscribe_order_stream")
