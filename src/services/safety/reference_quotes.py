"""9.5(R-47) — Data Distrust 쿼럼용 참조 시세 포트 + 어댑터 2종.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.3/§9(R-47), §10 "참조 시세
소스" 결정(PM, 2026-09-03).

`ReferenceQuoteProvider`는 `DataDistrustMonitor.check()`가 요구하는
`Ticker | None` 하나만 돌려주는 최소 포트다 — 실패·타임아웃은 예외를
올리지 않고 `None`으로 흡수한다(호출부가 quorum 부족을 "판정 불가"로
다루는 것과 동일 원칙, 참조 소스 하나 죽었다고 상위 로직이 예외 처리를
떠안지 않는다).

두 구현:
- `BitgetFuturesMarkPriceReference` — 같은 거래소의 제2 피드(현물 대신
  선물 마크가격). 완전히 독립적인 거래소는 아니라서 "약한 참조"다(같은
  거래소 인프라 장애에는 둘 다 영향받을 수 있음) — source_type을
  "reference"로 명시해 primary와 구분한다.
- `BinancePublicTickerReference` — 인증 불필요 공개 REST
  (`GET /api/v3/ticker/price`). 완전히 독립된 거래소라 "강한 참조"에
  가깝지만, 문서상 레이트리밋·가용성이 "미검증"이라 실패를 흔한 경로로
  취급한다(재시도하지 않고 그 틱은 그냥 None).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from src.data.models.market_data import Ticker

BINANCE_BASE_URL = "https://api.binance.com"
DEFAULT_REFERENCE_TIMEOUT_SECONDS = 2.0


class ReferenceQuoteProvider(Protocol):
    async def get_reference_ticker(self, symbol: str) -> Ticker | None: ...


class _FuturesTickerCapable(Protocol):
    async def get_futures_ticker(self, symbol: str) -> Ticker: ...


def _to_binance_symbol(symbol: str) -> str:
    """"BTC/USDT" -> "BTCUSDT" — Binance는 슬래시 없는 표기를 쓴다."""
    return symbol.replace("/", "")


class BitgetFuturesMarkPriceReference:
    def __init__(self, adapter: _FuturesTickerCapable) -> None:
        """`adapter`는 `get_futures_ticker(symbol) -> Ticker`를 가진
        BitgetAdapter(또는 이를 감싼 InstrumentedAdapter)다 — 구체
        클래스로 좁히지 않고 구조적 Protocol로만 요구한다
        (InstrumentedAdapter는 ExchangeAdapter를 상속하지 않고
        __getattr__ 위임이라 isinstance 검사가 항상 깨진다,
        src/exchanges/common/instrumented_adapter.py 참조)."""
        self._adapter = adapter

    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        try:
            ticker = await self._adapter.get_futures_ticker(symbol)
        except Exception:
            return None
        return ticker.model_copy(update={"source_type": "reference"})


class BinancePublicTickerReference:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_REFERENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(base_url=BINANCE_BASE_URL)
        self._timeout = timeout

    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        try:
            response = await self._client.get(
                "/api/v3/ticker/price",
                params={"symbol": _to_binance_symbol(symbol)},
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            price = Decimal(data["price"])
        except (httpx.HTTPError, KeyError, InvalidOperation):
            return None
        return Ticker(
            symbol=symbol,
            exchange="binance",
            price=price,
            bid=price,
            ask=price,
            volume_24h=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
            source_type="reference",
        )
