# 02. Exchange Adapter 공통 인터페이스 — v1.3

> **v1.3(2026-08-28) = BitgetAdapter/KISAdapter 실제 구현 완료(작업트리
> 6.1~6.11).** 두 Adapter 모두 httpx(+KIS는 OAuth2, Bitget은 HMAC-SHA256)로
> 직접 구현(CCXT 등 외부 라이브러리 없이 — 이미 검증된 raw 파서를 그대로
> 살리고 투명성을 유지하기 위한 선택). 실제 공식 API(Bitget v2, KIS Open
> API GitHub 공식 예제)를 조사해 확정. KISAdapter 생성자에 cano/acnt_prdt_cd
> 추가(§2.1 참조 — 계좌·주문 API 전부 필수 요구). 두 Adapter 모두 실제
> Demo/모의투자 API 키가 아직 없어(.env 비어있음) MockTransport로 조사된
> 응답을 재현해 테스트 — 실제 키 확보 시 라이브 왕복 검증 필요.

> **v1.2(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — `ExchangeCapability.asset_class: str`(단수) → `supported_asset_classes:
> list[AssetClass]`(복수, 01번 §1.0 AssetClass 참조)로 확장. "거래소 API가
> 지원하는 모든 상품을 취급하되, 지원하지 않는 조합은 명시적으로 비활성화"
> 원칙(capability-gated)을 §2.1-A로 신설 — Validator(03번 §3.3)가 이
> 리스트를 대조해 미지원 자산군 주문을 거부한다.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** §2.1~2.2가
> 정책문서(docx) 2장(2.1~2.5, 최상위 계층구조)과 번호가 겹치는 것을 발견 —
> 01/03번과 동일 라운드에 동일 조치.

> 근거: AIOS 문서 7.4(공통 거래 인터페이스), 7.6(Capability Model), 7.8(테스트넷 조사결과), 8.6-A(Cross-Asset Time-Gap Buffer)
> 상태: 추상 인터페이스(ABC)는 SCAFFOLD-READY. Bitget/KIS 구현체는 SCAFFOLD(단, 실제 주문 전송 로직은 Paper Trading 모드로만 검증 후 활성화)
> v3.1 갱신 — Phase 1 활성 대상: **Bitget(crypto) + 한국투자증권/KIS(kr_equity)**. Bithumb·SK증권은 보류.

```python
# src/exchanges/common/adapter.py
from __future__ import annotations
from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum
from typing import Awaitable, Callable

from src.data.models.market_data import Ticker, Candle, OrderBook
from src.data.models.trading import Order, Position, AccountBalance

# v3.1 신설(09번 §9.1 #4) — 이전까지 forward-ref로만 참조되고 미정의 상태였음
TickerCallback = Callable[[Ticker], Awaitable[None]]


class ExchangeCapability(BaseModel):
    """7.6 Capability Model — 각 Adapter가 스스로 선언하는 지원 기능.
    8.6-A-1 강화(v2.3) — Reference 피드 커버리지 등급도 여기서 선언한다.
    v3.1 확장 — KIS(주식) 추가로 자산군 구분 필드 신설.
    v1.4(ADR-2026-08-28) 확장 — 단일 asset_class(str)를 복수 지원 목록으로
    일반화. 한 거래소/브로커가 여러 자산군(예: KIS의 국내주식+해외주식+
    선물옵션)을 동시에 지원할 수 있다는 실제 운영 형태를 반영."""
    exchange_name: str
    supported_asset_classes: list["AssetClass"]  # 01번 §1.0 참조. 이 Adapter가
    # 실제로 거래 가능한 자산군 전체 — capability-gated 원칙(§2.1-A)의 근거 데이터.
    # 실제 값은 해당 브로커 공식 API 문서 확인 후 착수 시 확정(Draft로 시작).
    supports_spot: bool
    supports_futures: bool
    supports_options: bool = False  # v1.4 신설 — 옵션 지원 여부(기존 거래소는 기본 미지원)
    supports_leverage: bool
    supports_websocket: bool
    max_leverage: Decimal
    reference_feed_coverage: str  # "high" | "medium" | "low" — 8.6-A-1 레버리지 자동하향 연동
    has_official_sandbox: bool  # 7.8 조사결과: Bitget=True, Bithumb=False, KIS=True(모의투자)
    market_hours: Optional["MarketHours"] = None  # None이면 24시간(크립토). KRX는 09:00-15:30 KST
    min_order_size: dict[str, Decimal]  # symbol -> min size
    tick_size: dict[str, Decimal]


class MarketHours(BaseModel):
    """8.6-A Cross-Asset Time-Gap Buffer 판단에 사용 — 자산군별 개장시간 차이가
    통합 마진콜·연쇄청산을 유발할 수 있다는 원칙(12.2 Phase 5)의 실제 데이터 소스."""
    timezone: str  # "Asia/Seoul" 등
    open_time: str  # "09:00"
    close_time: str  # "15:30"
    trading_days: list[str]  # ["MON","TUE","WED","THU","FRI"]


class ExchangeAdapter(ABC):
    """7.4 — 상위 계층(Strategy/Portfolio/Risk)은 이 인터페이스만 알면 되고,
    거래소별 API 차이는 이 클래스의 구현체 내부로 격리된다.

    중요(7.9 원칙): 이 클래스의 어떤 메서드도 출금(withdraw) 기능을 포함하지 않는다.
    Trading Permission ≠ Withdrawal Permission — 출금은 이 Adapter의 책임 범위 밖이며
    7.10-A 비상 출금 프로토콜을 통해 별도의, 인간이 직접 수행하는 경로로만 처리된다.
    """

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
    async def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]: ...

    @abstractmethod
    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        """WebSocket 실시간 구독. supports_websocket=False인 거래소는
        NotImplementedError 발생시키고 REST 폴링으로 폴백(상위 계층 책임).

        재연결 책임(v3.1 신설, 09번 §9.1 #5): 연결 끊김 시 재연결·재구독은
        이 Adapter 구현체 내부 책임이다 — 호출부(Scanner 등 상위 계층)는
        재연결 로직을 알 필요가 없다. 단, 재연결 중에는 05번 문서의
        'market.distrust.entered'류 이벤트를 발행해 상위 계층에 '현재 데이터
        신뢰 불가' 상태를 알려야 한다(8.1-A Data Distrust Mode와 동일 원칙 적용).
        재연결 성공 시 해당 이벤트의 해제도 이 Adapter가 발행한다."""
        ...

    # ---- Account ----
    @abstractmethod
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...

    @abstractmethod
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order:
        """7.5 주문 멱등성 — UNKNOWN 상태 재확인 시 반드시 이 메서드로 거래소 실제 상태 재조회."""
        ...

    # ---- Trading (FROZEN 인접 — Execution Engine을 통해서만 호출됨) ----
    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        """실제 주문 전송. 이 메서드 자체는 SCAFFOLD(API 호출 매핑)이지만,
        '언제 이 메서드를 호출할지'를 결정하는 것은 FROZEN Zone(Executor)의 책임이다.
        이 메서드는 스스로 Risk Check를 수행하지 않는다 — 8.2-A Master Authority에 따라
        Risk Check는 이 메서드 호출 이전에 이미 완료되어 있어야 한다."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def modify_order(self, order_id: str, **kwargs) -> Order: ...

    # ---- Health Check (8.6-A-1-2 Split-Brain 방어에서 사용) ----
    @abstractmethod
    async def health_check(self) -> bool:
        """Watchdog(8.6-A-1-2)이 State DB와 무관하게 독립적으로 호출하는
        거래소 자체 응답성 확인용 메서드. 잔고 조회 등 경량 호출로 구현."""
        ...
```

## §2.0-A Capability-Gated 원칙 (v1.4 신설 — ADR-2026-08-28)

사용자 지침: "각 거래소별 API가 지원하는 모든 상품을 취급할 수 있어야 한다.
거래소 API가 제공하는 모든 상품을 거래할 수 있도록 각 거래소 API 구현 시에
사전에 파악하고 연결될 수 있도록, AIOS에서는 기본적으로 다 구현되어야 하고
거래소 API가 지원하지 않을 경우 비활성화되는 식으로 한다."

이를 다음 방식으로 구현한다:

1. **코어는 상위집합을 지원한다** — `Order`/`Position`(01번 §1.4)은 크립토
   현물부터 옵션·선물까지 표현 가능한 필드를 전부 갖는다.
2. **개별 Adapter는 자신의 실제 지원 범위만 선언한다** — 각 `ExchangeAdapter`
   구현체는 생성 시 `get_capabilities().supported_asset_classes`로 실제
   거래 가능한 자산군 목록을 반환한다. 이 목록은 **해당 브로커의 공식 API
   문서를 실제로 확인한 뒤** 착수 시 확정한다 — 지금 이 문서에서 특정
   거래소가 무엇을 지원하는지 추측해 하드코딩하지 않는다(예: KIS가 해외선물을
   지원하는지는 KISAdapter 착수 시점에 실제 KIS Open API 문서로 확인).
3. **미지원 조합은 명시적으로 거부한다** — Validator(03번 §3.3)가 주문 검증
   1단계로 `order.asset_class in adapter.get_capabilities().supported_asset_classes`를
   확인한다. 아니면 즉시 `ValidationResult(is_valid=False, errors=["UNSUPPORTED_ASSET_CLASS"])`
   — 침묵 실패나 다른 거래소로의 임의 폴백 금지(8.1-A Data Distrust와 동일한
   "판단 불가를 정상으로 취급하지 않는다" 원칙의 자산군 버전).
4. **새 거래소/브로커 추가 시 코어 타입을 바꾸지 않는다** — 새 Adapter는
   `supported_asset_classes`에 자신이 지원하는 `AssetClass` 값만 나열하면
   된다. 만약 정말로 새로운 자산군(01번 enum에 없는)이 필요해지면 그때
   `AssetClass`를 확장한다 — 매 거래소 추가마다 코어를 고치는 것이 아니라,
   드물게 자산군 자체가 새로 생길 때만 확장한다.

## §2.1 거래소별 구현체 골격

```python
# src/exchanges/bitget/adapter.py
class BitgetAdapter(ExchangeAdapter):
    """7.8 조사결과: 공식 Demo Trading API 존재.
    Demo 모드에서는 API Key에 'paptrading: 1' 헤더를 포함해 요청한다."""

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str, demo_mode: bool = True):
        # STATUS: **구현 완료(2026-08-28, 작업트리 6.1~6.8/6.11)** —
        # src/exchanges/bitget/{adapter,market_data_mixin,account_mixin,
        # trading_mixin}.py 참조. api_passphrase 추가(Bitget 서명에 필요 —
        # 원문에 누락돼 있었음).
        ...


# src/exchanges/bithumb/adapter.py
class BithumbAdapter(ExchangeAdapter):
    """7.8 조사결과: 공식 Sandbox 부재 확인됨.
    커뮤니티 라이브러리 경고 — 테스트 호출도 실거래를 유발할 수 있음.
    따라서 이 Adapter는 자체 MockBithumbAdapter(아래)와 반드시 함께 개발한다."""

    def __init__(self, api_key: str, api_secret: str):
        raise NotImplementedError("Phase 1 SCAFFOLD 착수 대상")


# src/exchanges/bithumb/mock_adapter.py
class MockBithumbAdapter(ExchangeAdapter):
    """7.8 설계 반영 — Bithumb은 공식 Sandbox가 없으므로,
    실제 API 호출 없이 응답 형식만 모사하는 이 클래스를 Bitget 대비 더 정교하게 구현한다.
    Paper Trading(Phase 1) 및 백테스트(Phase 2) 양쪽에서 사용."""

    def __init__(self, seed_balance: dict[str, Decimal]):
        raise NotImplementedError("Phase 1 SCAFFOLD 착수 대상 — 최우선 구현 권장")


# ============================================================
# ⚠️ 상태(v3.1): Bitget + KIS를 Phase 1 활성 대상으로 확정.
# Bithumb은 사용자 확정 전까지 "보류(Hold)" — 위 코드는 삭제하지 않고 남겨두되
# Phase 1 실제 착수 대상에서는 제외한다. 최종 확정 시 이 주석을 갱신할 것.
# ============================================================


# src/exchanges/kis/adapter.py
class KISAdapter(ExchangeAdapter):
    """한국투자증권(KIS) — 국내 유일 REST+WebSocket 공식 API, OAuth 2.0 인증.
    모의투자 계좌 공식 지원 — Bitget Demo와 대칭되는 테스트 경로(7.8 파이프라인 그대로 적용).

    asset_class="kr_equity"이므로 8.6-A Cross-Asset Time-Gap Buffer 적용 대상이다.
    Bitget(crypto, 24시간)과 KIS(주식, 09:00-15:30 KST)를 동시에 실거래하는 것은
    FROZEN Zone(Portfolio/Risk)의 Cross-Asset 로직이 실제 구현되기 전까지 금지한다 —
    이 Adapter 자체(SCAFFOLD)를 지금 개발하는 것과, 두 자산군을 동시에 라이브로
    돌리는 것(FROZEN, Phase 5 대상)은 서로 다른 문제다.

    인증: OAuth 2.0 2-legged. app_key/app_secret으로 access token 발급받아 사용.

    ⚠️ 검증 필요 가정(v3.1, 09번 §9.1 #8): Order.client_order_id를 영속적 멱등성 키로
    가정하고 설계했으나(7.5), 한국 증권사는 통상 거래소측 주문번호를 '일자별 채번'
    방식으로 발급하는 경우가 많다(KIS 실제 방식은 계좌 신청 후 API 문서로 확인 필요).
    이 경우 exchange_order_id와 client_order_id의 대응 관계, 그리고 자정을 넘는
    미체결 주문의 상태 추적 방식을 KIS 실제 문서 확인 후 이 섹션에 반영한다.
    """

    def __init__(
        self, app_key: str, app_secret: str, cano: str, acnt_prdt_cd: str,
        is_paper_trading: bool = True,
    ):
        # STATUS: **구현 완료(2026-08-28, 작업트리 6.9/6.10)** — src/exchanges/kis/
        # {adapter,market_data_mixin,account_mixin,trading_mixin}.py 참조.
        #
        # 편차 — cano/acnt_prdt_cd 추가: KIS 공식 GitHub 예제(github.com/
        # koreainvestment/open-trading-api)를 실제 조사한 결과, 모든 계좌·주문
        # API가 종합계좌번호(CANO, 8자리)와 계좌상품코드(ACNT_PRDT_CD, 2자리)를
        # 필수 파라미터로 요구한다는 것을 발견 — app_key/app_secret만으로는
        # 호출 불가능했다.
        #
        # get_capabilities()가 market_hours 필드(MarketHours)를 채워 반환한다 —
        # 별도의 get_market_hours() 메서드는 두지 않는다(v3.1, 09번 §9.1 #11).
        #
        # v1.4(ADR-2026-08-28) — Phase 1은 KR_EQUITY만 확정(§6.1 mvp_scope).
        # KIS Open API의 해외주식·선물옵션 등 나머지 지원 범위는 공식 문서
        # 재확인 전까지 Draft로 남김(06번 §6.1-A 표 참조).
        ...
```

## §2.2 자산군 혼재 시 주의사항 (v3.1 신설, v1.4 확장 대상 갱신)

Bitget(crypto)과 KIS(kr_equity, 및 향후 확장될 KIS의 다른 자산군)를 동일
포트폴리오에서 다룰 경우, 상위 문서 8.6-A Cross-Asset Time-Gap Buffer 원칙이
적용된다 — v1.4부터는 "crypto vs kr_equity" 2종만이 아니라 `AssetClass`(01번
§1.0)에 속한 어떤 조합이든 동일 원칙이 적용된다(예: KR_EQUITY와 US_EQUITY도
개장시간대가 다르므로 동일하게 취급):

- **지금(Phase 1 SCAFFOLD) 해도 되는 것**: 두 Adapter의 인터페이스 구현, 각각 독립적인 Paper Trading 검증.
- **아직 하면 안 되는 것**: 두 거래소 포지션을 하나의 Risk Engine 계산에 실시간으로 합산해 실거래 자금을 굴리는 것 — 이는 FROZEN Zone(Portfolio/Risk)의 Cross-Asset 로직이 실제로 구현·검증된 이후(12.2 Phase 5 원칙)로 유지한다.
- 두 Adapter의 `asset_class` 필드를 Portfolio Engine이 반드시 구분해서 다루도록 설계해야 하며, 이 구분 자체는 지금 데이터 모델 단계에서 미리 준비해두는 것이 맞다(그래서 위 `ExchangeCapability.asset_class`를 지금 추가했다).

