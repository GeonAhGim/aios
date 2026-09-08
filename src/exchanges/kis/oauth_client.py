"""6.9/L4-21 — KIS OAuth2 토큰 발급/캐싱 + 서명·전송 공통 로직.

Spec: 02_exchange_adapter_v1.2.md#§2.1,
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-21

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

`adapter.py`(300줄 캡, L4-21 신규 로직 추가로 초과)가 토큰/전송 로직을 이
파일로 분리했다(순수 이동, 동작 변경 없음 — bitget/trading_query_mixin.py
분리와 동일 판단). `_resolve_tr_id`/`_PAPER_SWAP_PREFIXES`만은 `adapter.py`
에 남긴다 — `tests/unit/exchanges/kis/test_overseas_futureoption_tr_reference
.py::test_paper_swap_rule_mutation_breaks_tr_id_identity`가
`monkeypatch.setattr(adapter_module, "_PAPER_SWAP_PREFIXES", ...)`로 그
모듈 전역을 직접 패치한다 — 전역 참조는 정의된 모듈 기준으로 묶이므로
(closure가 아니라 함수가 정의된 모듈의 네임스페이스), `_resolve_tr_id`를
여기로 옮기면 그 몽키패치가 조용히 무효화된다.

재시도·백오프·서킷·클럭보정은 여기서 재구현하지 않고 `ResilientTransport`
(L4-12, common/transport.py)에 위임한다. 토큰 캐시는 NH(BR-10,
oauth_http.py)가 먼저 올린 `MonotonicTokenCache`를 재사용한다(이 리프가
"KIS 이관은 후속 리프 몫"이었던 그 후속 리프다) — `asyncio.Lock`으로 감싸
만료 상태에서 동시에 여러 코루틴이 들어와도 토큰 발급 엔드포인트는 정확히
1회만 불린다(double-checked locking, DoD a).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.oauth_http import MonotonicTokenCache
from src.exchanges.common.transport import ResilientTransport

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"


class _KISTokenTransportMixin:
    """OAuth2 토큰 발급/캐싱 + 요청 전송 공통 로직. `adapter.py`의
    `_KISHTTPClient(_KISTokenTransportMixin)`이 `_resolve_tr_id`를 얹어
    완성한다 — `_headers()`가 부르는 `self._resolve_tr_id(tr_id)`는 인스턴스
    속성 조회라 어느 파일에 있든 정상 동작한다(모듈 docstring 참조)."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        cano: str,
        acnt_prdt_cd: str,
        *,
        is_paper_trading: bool = True,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._cano = cano
        self._acnt_prdt_cd = acnt_prdt_cd
        self._is_paper_trading = is_paper_trading
        base_url = PAPER_BASE_URL if is_paper_trading else REAL_BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._transport = ResilientTransport(venue="kis", sleep=sleep_fn or asyncio.sleep)
        self._token_cache = MonotonicTokenCache()
        self._token_lock = asyncio.Lock()

    async def _ensure_token(self) -> str:
        cached = self._token_cache.get()
        if cached is not None:
            return cached
        async with self._token_lock:
            # double-checked — lock 대기 중 다른 코루틴이 이미 발급했을 수 있다.
            cached = self._token_cache.get()
            if cached is not None:
                return cached
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        async def send_once() -> httpx.Response:
            return await self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                },
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )

        try:
            response = await self._transport.request(send_once)
        except ExchangeError as exc:
            raise FatalExchangeError(f"KIS 토큰 발급 실패: {exc}") from exc

        data = response.json()
        token: str = data["access_token"]
        # KIS는 만료시각을 "YYYY-MM-DD HH:MM:SS" 문자열로 주지만(1일 유효),
        # 여기서는 보수적으로 23시간만 캐싱해 만료 직전 재사용을 피한다.
        self._token_cache.set(token, 23 * 3600)
        return token

    def _invalidate_token(self) -> None:
        # ttl=0 → 다음 get()은 항상 만료로 본다(MonotonicTokenCache.get()의
        # `<` 비교는 같은 순간이어도 통과하지 않는다 — 경합 없이 결정적).
        self._token_cache.set("", 0.0)

    def _resolve_tr_id(self, tr_id: str) -> str:
        """실전/모의 tr_id 치환. `adapter.py`의 `_KISHTTPClient`가 실제
        구현을 얹는다(모듈 docstring 참조) — 이 스텁이 직접 불릴 일은 없다."""
        raise NotImplementedError

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

    def _classify_body(self, response: httpx.Response) -> ExchangeError | None:
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=True,
                venue="kis",
                http_status=response.status_code,
                message=f"KIS 응답이 JSON이 아님: {response.text}",
            )
        if data.get("rt_cd") == "0":
            return None
        return ExchangeError(
            ExchangeErrorKind.UNKNOWN_RESPONSE,
            retryable=True,
            venue="kis",
            http_status=response.status_code,
            message=f"KIS API 오류: {data}",
        )

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """DoD(b) — 401(AUTH)은 토큰을 무효화하고 원요청을 정확히 1회만
        재시도한다. 재시도에서도 401이면 더 반복하지 않고 그대로 예외로
        표면화한다. HTTP 상태코드/네트워크 재시도·백오프는
        `ResilientTransport`(DoD c)가 맡고 여기서 다시 구현하지 않는다."""
        retried_after_auth = False
        while True:
            headers = await self._headers(tr_id)

            async def send_once(headers: dict[str, str] = headers) -> httpx.Response:
                return await self._client.request(
                    method, path, params=params, json=body, headers=headers
                )

            try:
                response = await self._transport.request(
                    send_once, classify_body=self._classify_body
                )
            except ExchangeError as exc:
                if exc.kind is ExchangeErrorKind.AUTH and not retried_after_auth:
                    self._invalidate_token()
                    retried_after_auth = True
                    continue
                if exc.retryable:
                    raise RetryableExchangeError(str(exc)) from exc
                raise FatalExchangeError(str(exc)) from exc
            return dict(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
