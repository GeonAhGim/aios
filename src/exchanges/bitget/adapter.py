"""6.3 — BitgetAdapter.__init__ + 인증.

Spec: 02_exchange_adapter_v1.2.md#§2.1

인증 방식(2026-08-28 Bitget 공식 문서 조사 확인): prehash = timestamp +
method.upper() + requestPath(+ "?"+queryString) + body, HMAC-SHA256 후
base64. 헤더: ACCESS-KEY/ACCESS-SIGN/ACCESS-TIMESTAMP/ACCESS-PASSPHRASE +
Content-Type: application/json + locale. Demo 모드는 paptrading: 1 헤더
추가(공식적으로는 USDT-FUTURES 문서에서 확인 — 스팟 데모도 동일 메커니즘을
쓰는 것으로 보이나 실제 Demo API 키로 라이브 검증 전까지는 확정 아님).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.exchanges.bitget.account_mixin import BitgetAccountMixin
from src.exchanges.bitget.market_data_mixin import BitgetMarketDataMixin
from src.exchanges.bitget.trading_mixin import BitgetTradingMixin
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability

BASE_URL = "https://api.bitget.com"

# 인증 실패로 간주해 재시도하지 않을 에러코드(문서상 확인된 것만 — 나머지는
# 안전한 기본값으로 재시도 가능 취급, RetryableExchangeError). 실제 Demo API
# 키 확보 후 라이브 검증하며 목록을 넓혀야 한다.
_FATAL_ERROR_CODES = {"40012", "40037"}  # 서명 오류 / API 키 없음(문서 조사 기준)


class _BitgetHTTPClient:
    """REST 요청 서명·전송 공통 로직. Mixin들이 self._request()로 접근한다."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        *,
        demo_mode: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._demo_mode = demo_mode
        self._client = http_client or httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        prehash = timestamp + method.upper() + request_path + body
        mac = hmac.new(self._api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        headers = {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": self._sign(timestamp, method, request_path, body),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }
        if self._demo_mode:
            headers["paptrading"] = "1"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_string = ""
        if params:
            # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #18a) 반영 — 서명용
            # 쿼리스트링을 수동으로 f"{k}={v}" 조합하면 httpx가 실제 전송 시
            # 적용하는 퍼센트인코딩과 어긋날 수 있다(지금 쓰는 값은 전부
            # 영숫자라 우연히 일치했을 뿐). httpx.QueryParams로 인코딩한
            # 문자열을 서명과 실제 전송 양쪽에 동일하게 재사용한다.
            query_string = "?" + str(httpx.QueryParams(params))
        body_str = json.dumps(body) if body else ""
        request_path = path + query_string
        headers = self._headers(method, request_path, body_str)

        try:
            response = await self._client.request(
                method, request_path, content=body_str or None, headers=headers
            )
        except httpx.TransportError as exc:
            raise RetryableExchangeError(f"Bitget 요청 전송 실패: {exc}") from exc

        data: dict[str, Any] = response.json()
        code = data.get("code")
        if code != "00000":
            if code in _FATAL_ERROR_CODES:
                raise FatalExchangeError(f"Bitget 인증/서명 오류: {data}")
            raise RetryableExchangeError(f"Bitget API 오류: {data}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


class BitgetAdapter(
    _BitgetHTTPClient,
    BitgetMarketDataMixin,
    BitgetAccountMixin,
    BitgetTradingMixin,
    ExchangeAdapter,
):
    """7.8 조사결과: 공식 Demo Trading API 존재."""

    @property
    def is_paper_trading(self) -> bool:
        return self._demo_mode

    @property
    def is_sandboxed(self) -> bool:
        """레드팀 감사(2026-09-01-08) — 생성자의 demo_mode 그대로 노출.
        `is_paper_trading`과 같은 값(이 어댑터는 하나의 플래그로 두 신호를
        전부 만족시킨다) — `_headers()`가 paptrading 헤더를 붙이는 바로
        그 조건과 동일하다."""
        return self._demo_mode

    def get_capabilities(self) -> ExchangeCapability:
        """v1.4(ADR-2026-08-28) — Phase 1 실거래 대상은 crypto 현물뿐(06번
        §6.1). Bitget이 futures/margin도 지원하지만 Phase 1 스콥 밖이라
        여기서는 선언하지 않는다 — 선언 자체가 "이 Adapter로 거래 가능"이라는
        신호이므로 스콥을 넘는 걸 미리 열어두지 않는다(capability-gated
        원칙, §2.0-A)."""
        return ExchangeCapability(
            exchange_name="bitget",
            supported_asset_classes=[AssetClass.CRYPTO],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=True,
            max_leverage=Decimal("1"),
            reference_feed_coverage="high",
            has_official_sandbox=True,
        )
