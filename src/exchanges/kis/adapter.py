"""6.9 — KISAdapter.__init__ + OAuth 인증.

Spec: 02_exchange_adapter_v1.2.md#§2.1

인증/엔드포인트(2026-08-28 KIS 공식 GitHub 예제
github.com/koreainvestment/open-trading-api 소스코드 확인):
- OAuth2: POST /oauth2/tokenP, body {grant_type:"client_credentials",
  appkey, appsecret} → {access_token, access_token_token_expired}(1일 유효)
- Base URL: 실전 https://openapi.koreainvestment.com:9443,
  모의투자 https://openapivts.koreainvestment.com:29443
- 요청 헤더: Content-Type/Accept/charset + authorization: Bearer {token} +
  appkey + appsecret + tr_id + custtype: "P"
- tr_id 실전/모의 변환: 앞글자가 T/J/C면 모의투자는 'V'로 치환(예:
  TTTC8434R → VTTC8434R). 시세조회(F로 시작)류는 실전/모의 동일 tr_id.
- 응답 포맷: {rt_cd: "0"(성공)|기타, msg_cd, msg1, output/output1/output2}

편차: 02번 스펙 원문 시그니처는 __init__(app_key, app_secret,
is_paper_trading)였으나, 실제로는 모든 계좌/주문 API가 종합계좌번호(CANO)와
계좌상품코드(ACNT_PRDT_CD)를 필수 파라미터로 요구한다는 것을 조사 중
발견 — 생성자에 cano/acnt_prdt_cd 추가.
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability, MarketHours
from src.exchanges.kis.account_mixin import KISAccountMixin
from src.exchanges.kis.domestic_stock_extra_mixin import KISDomesticStockExtraMixin
from src.exchanges.kis.market_data_mixin import KISMarketDataMixin
from src.exchanges.kis.trading_mixin import KISTradingMixin

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"

# 앞글자가 이 중 하나면 모의투자 tr_id는 'V'로 치환한다(실거래/정정취소류).
# 시세조회(F로 시작 등)는 치환 대상 아님 — 실전/모의 동일 tr_id 사용.
_PAPER_SWAP_PREFIXES = ("T", "J", "C")


class _KISHTTPClient:
    """OAuth2 토큰 발급/캐싱 + 요청 전송 공통 로직. Mixin들이 self._request()로
    접근한다."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        cano: str,
        acnt_prdt_cd: str,
        *,
        is_paper_trading: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._cano = cano
        self._acnt_prdt_cd = acnt_prdt_cd
        self._is_paper_trading = is_paper_trading
        base_url = PAPER_BASE_URL if is_paper_trading else REAL_BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _resolve_tr_id(self, tr_id: str) -> str:
        if self._is_paper_trading and tr_id[0] in _PAPER_SWAP_PREFIXES:
            return "V" + tr_id[1:]
        return tr_id

    async def _ensure_token(self) -> str:
        if self._access_token is not None and time.monotonic() < self._token_expires_at:
            return self._access_token

        response = await self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        if response.status_code != 200:
            raise FatalExchangeError(f"KIS 토큰 발급 실패: {response.status_code} {response.text}")

        data = response.json()
        self._access_token = data["access_token"]
        # KIS는 만료시각을 "YYYY-MM-DD HH:MM:SS" 문자열로 주지만(1일 유효),
        # 여기서는 보수적으로 23시간만 캐싱해 만료 직전 재사용을 피한다.
        self._token_expires_at = time.monotonic() + 23 * 3600
        return self._access_token

    async def _headers(self, tr_id: str) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "text/plain",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": self._resolve_tr_id(tr_id),
            "custtype": "P",
        }

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = await self._headers(tr_id)
        try:
            response = await self._client.request(
                method, path, params=params, json=body, headers=headers
            )
        except httpx.TransportError as exc:
            raise RetryableExchangeError(f"KIS 요청 전송 실패: {exc}") from exc

        data: dict[str, Any] = response.json()
        if data.get("rt_cd") != "0":
            if response.status_code in (401, 403):
                raise FatalExchangeError(f"KIS 인증 오류: {data}")
            raise RetryableExchangeError(f"KIS API 오류: {data}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


class KISAdapter(
    _KISHTTPClient,
    KISMarketDataMixin,
    KISAccountMixin,
    KISTradingMixin,
    KISDomesticStockExtraMixin,
    ExchangeAdapter,
):
    """한국투자증권(KIS) — 국내 REST+WebSocket 공식 API, OAuth 2.0 인증.

    ⚠️ 검증 필요 가정(09번 §9.1 #8과 동일 원칙) — Order.client_order_id를
    영속적 멱등성 키로 가정한 01번 설계와 달리, KIS는 주문 시 자체 채번한
    ODNO(주문번호)를 반환하며 client_order_id 개념 자체가 없다 — 이
    Adapter는 client_order_id를 KIS에 전달하지 않는다(전달할 방법이 없음).
    """

    @property
    def is_paper_trading(self) -> bool:
        return self._is_paper_trading

    @property
    def is_sandboxed(self) -> bool:
        """레드팀 감사(2026-09-01-08) — 생성자의 is_paper_trading 그대로 노출."""
        return self._is_paper_trading

    def get_capabilities(self) -> ExchangeCapability:
        """v1.4(ADR-2026-08-28) — 06번 §6.1-A: Phase 1은 KR_EQUITY만 확정,
        해외주식·선물옵션 등 KIS의 나머지 지원 범위는 Draft(공식 문서
        재확인 전까지 선언하지 않음 — capability-gated 원칙, §2.0-A)."""
        return ExchangeCapability(
            exchange_name="kis",
            supported_asset_classes=[AssetClass.KR_EQUITY],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=False,  # WS는 미구현(6.9/6.10 스콥 밖, 향후 확장)
            max_leverage=Decimal("1"),
            reference_feed_coverage="high",
            has_official_sandbox=True,
            market_hours=MarketHours(
                timezone="Asia/Seoul",
                open_time="09:00",
                close_time="15:30",
                trading_days=["MON", "TUE", "WED", "THU", "FRI"],
            ),
        )
