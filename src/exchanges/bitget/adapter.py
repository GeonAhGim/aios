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
from src.exchanges.bitget.error_codes import SUCCESS_CODE, classify_body_code
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
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.common.transport import ResilientTransport
from src.exchanges.common.types import ExchangeCapability

BASE_URL = "https://api.bitget.com"

# FULL_AUDIT_2026-09-02.md §2-B ① — 429/5xx는 일시적 장애로 보고 재시도,
# 그 외 4xx는 재시도해도 성공할 가능성이 없어(잘못된 요청/인증) 즉시 Fatal.
# L4-12 — 이전엔 이 파일이 직접 backoff 루프를 돌렸다(수동 attempt 카운터 +
# 지수 백오프). 이제 ResilientTransport(L4-11 5모듈 조립, task-1015)가 같은
# 정책을 수행한다 — max_attempts=4(최초 1회 + 재시도 3회), base=1.0/cap=30.0로
# 이전 상수(_MAX_RETRIES=3, _MAX_BACKOFF_SECONDS=30.0)와 동일한 지연을
# 재현한다. rng를 상수 1.0으로 고정하는 이유는 아래 생성자 참고.
_RETRY_POLICY = RetryPolicy(max_attempts=4, base=1.0, cap=30.0)


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
        # L4-12(task-1015) — 재시도/백오프/서버시간 보정을 직접 구현하지
        # 않고 ResilientTransport(L4-11 5모듈 조립)에 위임한다. rng를 상수
        # 1.0으로 고정하는 이유: http_policy.backoff_delay의 full-jitter는
        # `[0, ceiling)`을 rng()로 균일 샘플링하는데, rng() == 1.0이면
        # 매번 ceiling 값 그대로(=1.0, 2.0, 4.0, ... 지수 백오프)가 나와
        # 기존 test_bitget_adapter.py의 결정론적 sleep 값 검증(예: 429
        # 재시도 시 [1.0, 2.0])과 정확히 일치한다 — 실제 지터가 필요해지면
        # ADR 없이 여기 rng만 `random.random`으로 바꾸면 된다(호출부 교체
        # 지점이 이 한 줄로 국소화돼 있음).
        self._transport = ResilientTransport(
            venue="bitget",
            retry_policy=_RETRY_POLICY,
            rng=lambda: 1.0,
            sleep=sleep_fn or asyncio.sleep,
        )

    @property
    def _time_offset_ms(self) -> float:
        """하위호환 프로퍼티 — 실제 상태는 `self._transport.clock`(ServerClock,
        L4-11)이 갖고 있다. 기존 테스트(test_bitget_adapter.py)가 이 속성을
        직접 읽으므로 이름은 유지한다."""
        return self._transport.clock.offset_ms

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        prehash = timestamp + method.upper() + request_path + body
        mac = hmac.new(self._api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def _fetch_server_time_ms(self) -> int:
        response = await self._client.get("/api/v2/public/time")
        data = response.json()
        return int(data["data"]["serverTime"])

    async def sync_server_time(self) -> None:
        """FULL_AUDIT §2-B ① — Bitget 서버시간과의 오프셋을 측정해 이후
        요청의 ACCESS-TIMESTAMP에 반영한다. 실제 계산은 `ServerClock.sync()`
        (L4-11, clock_sync.py)가 왕복지연 절반 보정으로 수행한다. 실패해도
        예외를 올리지 않는다(8.3 원칙 — 시간 동기화 실패가 거래 자체를
        막으면 안 됨; instrumented_adapter.py가 이 보장에 의존한다) — 네트워크
        /파싱 실패는 오프셋 갱신 이전 상태(보통 0)로 남고, skew 초과
        (`ExchangeError(CLOCK_SKEW)`)는 이미 갱신된 오프셋을 진단용으로
        남긴 채 여기서 삼킨다(ADR-2026-08-29-E — 이 리프는 demo/paper
        경로만 열고, LIVE 서명 경로는 별도 가드가 차단한다)."""
        try:
            await self._transport.clock.sync(self._fetch_server_time_ms)
        except Exception:  # noqa: BLE001 — 위 docstring 참고, 삼키는 게 계약
            pass

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        timestamp = str(self._transport.clock.now_ms())
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

    def _classify_body(self, response: httpx.Response) -> ExchangeError | None:
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            # error_taxonomy 기본값(UNKNOWN_RESPONSE → retryable=False)을
            # 여기서는 `retryable=True`로 명시 override한다 — 이 리프 이전부터
            # 비JSON 응답을 일시적 장애로 보고 RetryableExchangeError를
            # 던져온 기존 계약(test_bitget_adapter.py::
            # test_request_raises_retryable_on_non_json_response, 무수정
            # 유지 대상)을 깨지 않기 위함이다. `_classify_body`는
            # ResilientTransport에서 단발 평가만 되므로(재시도 루프에 안
            # 태움) 이 override가 실제로 재시도 폭주를 일으키지 않는다.
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=True,
                venue="bitget",
                http_status=response.status_code,
                message=f"Bitget 응답이 JSON이 아님: {response.text}",
            )
        code = data.get("code")
        if code == SUCCESS_CODE:
            return None
        return ExchangeError(
            classify_body_code(code),
            venue="bitget",
            http_status=response.status_code,
            venue_code=code,
            message=f"Bitget API 오류: {data}",
        )

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

        async def send_once() -> httpx.Response:
            # 시도마다 새로 호출된다 — ACCESS-TIMESTAMP/ACCESS-SIGN이 매
            # 시도마다 갱신돼야 하므로 헤더를 캡처하지 않고 매번 새로 만든다.
            headers = self._headers(method, request_path, body_str)
            return await self._client.request(
                method, request_path, content=body_str or None, headers=headers
            )

        try:
            response = await self._transport.request(send_once, classify_body=self._classify_body)
        except ExchangeError as exc:
            if exc.retryable:
                raise RetryableExchangeError(str(exc)) from exc
            raise FatalExchangeError(str(exc)) from exc

        data: dict[str, Any] = response.json()
        return data

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
