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

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.exchanges.bitget.account_mixin import BitgetAccountMixin
from src.exchanges.bitget.broker_mixin import BitgetBrokerMixin
from src.exchanges.bitget.convert_mixin import BitgetConvertMixin
from src.exchanges.bitget.copy_trading_mixin import BitgetCopyTradingMixin
from src.exchanges.bitget.earn_mixin import BitgetEarnMixin
from src.exchanges.bitget.futures_account_mixin import BitgetFuturesAccountMixin
from src.exchanges.bitget.futures_market_mixin import BitgetFuturesMarketMixin
from src.exchanges.bitget.futures_trading_mixin import BitgetFuturesTradingMixin
from src.exchanges.bitget.grid_mixin import BitgetGridMixin
from src.exchanges.bitget.inst_loan_mixin import BitgetInstLoanMixin
from src.exchanges.bitget.loan_mixin import BitgetLoanMixin
from src.exchanges.bitget.margin_mixin import BitgetMarginMixin
from src.exchanges.bitget.market_data_mixin import BitgetMarketDataMixin
from src.exchanges.bitget.p2p_mixin import BitgetP2PMixin
from src.exchanges.bitget.strategy_mixin import BitgetStrategyMixin
from src.exchanges.bitget.subaccount_mixin import BitgetSubaccountMixin
from src.exchanges.bitget.tax_mixin import BitgetTaxMixin
from src.exchanges.bitget.trading_mixin import BitgetTradingMixin
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability

BASE_URL = "https://api.bitget.com"

# 인증 실패로 간주해 재시도하지 않을 에러코드(문서상 확인된 것만 — 나머지는
# 안전한 기본값으로 재시도 가능 취급, RetryableExchangeError). 실제 Demo API
# 키 확보 후 라이브 검증하며 목록을 넓혀야 한다.
_FATAL_ERROR_CODES = {"40012", "40037"}  # 서명 오류 / API 키 없음(문서 조사 기준)

# FULL_AUDIT_2026-09-02.md §2-B ① — 429/5xx는 일시적 장애로 보고 재시도,
# 그 외 4xx는 재시도해도 성공할 가능성이 없어(잘못된 요청/인증) 즉시 Fatal.
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 30.0


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
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._demo_mode = demo_mode
        self._client = http_client or httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)
        self._sleep_fn = sleep_fn or asyncio.sleep
        # FULL_AUDIT §2-B ① 서버시간 오프셋 — 기본 0(로컬 시계 그대로 사용),
        # sync_server_time()을 명시적으로 호출해야 갱신된다. 매 요청마다
        # 자동으로 동기화하면 기존 MockTransport 테스트 전부(14개 파일)가
        # 추가 요청 하나를 더 받게 돼 깨진다 — 그 정도로 매 요청 지연을
        # 감수할 가치도 없다(클럭 드리프트는 보통 안정적이라 1회 동기화로
        # 충분). Executor 시작 시 또는 서명 오류가 반복될 때 호출부가
        # 명시적으로 부르는 옵션 메서드로 둔다(정책은 호출부 책임 원칙,
        # borrow_margin() 등 기존 확장 메서드 docstring과 동일 판단).
        self._time_offset_ms = 0

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        prehash = timestamp + method.upper() + request_path + body
        mac = hmac.new(self._api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def sync_server_time(self) -> None:
        """FULL_AUDIT §2-B ① — Bitget 서버시간과의 오프셋을 측정해 이후
        요청의 ACCESS-TIMESTAMP에 반영한다. 왕복지연 절반만큼 보정
        (요청 전송 직전/직후 로컬시각의 평균을 서버 응답 시각과 비교하는
        일반적인 NTP류 관례) — 완벽하지 않지만 클럭 드리프트로 인한
        서명 타임스탬프 거부(40012류)를 크게 줄인다. 실패해도 예외를
        올리지 않고 오프셋 0으로 안전하게 유지한다(8.3 원칙 — 시간 동기화
        실패가 거래 자체를 막으면 안 됨)."""
        try:
            local_before = time.time()
            response = await self._client.get("/api/v2/public/time")
            local_after = time.time()
            data = response.json()
            server_time_ms = int(data["data"]["serverTime"])
            local_mid_ms = (local_before + local_after) / 2 * 1000
            self._time_offset_ms = int(server_time_ms - local_mid_ms)
        except Exception:  # noqa: BLE001 — 동기화 실패는 오프셋 0 유지로 안전하게 수렴
            self._time_offset_ms = 0

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000) + self._time_offset_ms)
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

        backoff = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            headers = self._headers(method, request_path, body_str)
            try:
                response = await self._client.request(
                    method, request_path, content=body_str or None, headers=headers
                )
            except httpx.TransportError as exc:
                raise RetryableExchangeError(f"Bitget 요청 전송 실패: {exc}") from exc

            # FULL_AUDIT §2-B ① — 이전에는 HTTP 상태코드를 전혀 보지 않고
            # 바로 response.json()으로 넘어갔다. 429(rate limit)/5xx(서버
            # 장애)는 일시적이라 지수 백오프로 재시도, Retry-After 헤더가
            # 있으면 그 값을 우선한다.
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == _MAX_RETRIES:
                    raise RetryableExchangeError(
                        f"Bitget HTTP {response.status_code} 재시도 소진: {response.text}"
                    )
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff
                await self._sleep_fn(wait)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue

            # 그 외 4xx는 재시도해도 성공할 가능성이 없다(잘못된 요청/
            # 인증 오류) — 즉시 Fatal.
            if response.status_code >= 400:
                raise FatalExchangeError(
                    f"Bitget HTTP {response.status_code}: {response.text}"
                )

            try:
                data: dict[str, Any] = response.json()
            except ValueError as exc:
                raise RetryableExchangeError(
                    f"Bitget 응답이 JSON이 아님: {response.text}"
                ) from exc

            code = data.get("code")
            if code != "00000":
                if code in _FATAL_ERROR_CODES:
                    raise FatalExchangeError(f"Bitget 인증/서명 오류: {data}")
                raise RetryableExchangeError(f"Bitget API 오류: {data}")
            return data

        raise RetryableExchangeError("Bitget 요청 재시도 루프 종료(도달 불가 경로)")

    async def aclose(self) -> None:
        await self._client.aclose()


class BitgetAdapter(
    _BitgetHTTPClient,
    BitgetMarketDataMixin,
    BitgetAccountMixin,
    BitgetTradingMixin,
    BitgetMarginMixin,
    BitgetFuturesMarketMixin,
    BitgetFuturesAccountMixin,
    BitgetFuturesTradingMixin,
    BitgetConvertMixin,
    BitgetSubaccountMixin,
    BitgetTaxMixin,
    BitgetEarnMixin,
    BitgetLoanMixin,
    BitgetGridMixin,
    BitgetStrategyMixin,
    BitgetP2PMixin,
    BitgetBrokerMixin,
    BitgetCopyTradingMixin,
    BitgetInstLoanMixin,
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
