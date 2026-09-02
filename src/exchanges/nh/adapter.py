"""NHAdapter.__init__ + OAuth2 인증.

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§2

인증/엔드포인트(2026-09-03 NH투자증권 공식 Python SDK 소스코드 확인,
github.com/PLUG-OpenAPI/nhplug-sdk):
- OAuth2: POST /oauth2/token, `application/x-www-form-urlencoded`,
  params {appkey, appsecretkey, grant_type:"client_credentials",
  scope:"oob"} → {access_token, expires_in}(기본 86400초)
- Base URL: 실전 https://api.nhplug.com:8443,
  모의투자 https://moapi.nhplug.com:8443
- 요청 헤더: x-client-id(appkey) / x-client-secret(appsecretkey) /
  authorization: Bearer {token} / content-type — Bitget/KIS와 달리
  tr_id를 헤더로 보내지 않는다(엔드포인트 경로 자체가 TR을 구분).
- 응답 포맷: HTTP 200이어도 실패일 수 있다 — 바디의 `rsp_cd`가
  "00000"(SDK 관례상 "00166"/"00221"/"13578"도 성공 취급) 또는
  `rsp_msg`에 "완료" 포함 시 성공.

편차: 02번 스펙 원문 시그니처는 계좌번호를 미리 받지 않지만, NH도
KIS와 마찬가지로 모든 계좌 관련 API가 `act_no`(계좌번호)를 요구한다
(공식 SDK 확인) — 생성자에 act_no 추가(KIS의 cano/acnt_prdt_cd와 동일
판단, 다만 NH는 계좌번호가 단일 문자열이라 분리하지 않음).
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
from src.exchanges.nh.account_mixin import NHAccountMixin
from src.exchanges.nh.market_data_mixin import NHMarketDataMixin
from src.exchanges.nh.trading_mixin import NHTradingMixin

REAL_BASE_URL = "https://api.nhplug.com:8443"
PAPER_BASE_URL = "https://moapi.nhplug.com:8443"

# SDK 관례상 성공으로 취급하는 rsp_cd 값(02e 스펙 §2 확인) — 정확한 의미
# 차이(부분성공 등)는 라이브 검증 필요.
_SUCCESS_CODES = {"00000", "00166", "00221", "13578"}


class _NHHTTPClient:
    """OAuth2 토큰 발급/캐싱 + 요청 전송 공통 로직. Mixin들이 self._request()로
    접근한다."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        act_no: str,
        *,
        is_paper_trading: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._act_no = act_no
        self._is_paper_trading = is_paper_trading
        base_url = PAPER_BASE_URL if is_paper_trading else REAL_BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        if self._access_token is not None and time.monotonic() < self._token_expires_at:
            return self._access_token

        response = await self._client.post(
            "/oauth2/token",
            params={
                "appkey": self._app_key,
                "appsecretkey": self._app_secret,
                "grant_type": "client_credentials",
                "scope": "oob",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise FatalExchangeError(f"NH 토큰 발급 실패: {response.status_code} {response.text}")

        data = response.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        # 만료 직전 재사용을 피하려 60초 여유를 둔다(문서화된 안전 마진).
        self._token_expires_at = time.monotonic() + max(expires_in - 60, 0)
        return self._access_token

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "x-client-id": self._app_key,
            "x-client-secret": self._app_secret,
            "authorization": f"Bearer {token}",
            "content-type": "application/json; charset=UTF-8",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = await self._headers()
        try:
            response = await self._client.request(
                method, path, params=params, json=body, headers=headers
            )
        except httpx.TransportError as exc:
            raise RetryableExchangeError(f"NH 요청 전송 실패: {exc}") from exc

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise RetryableExchangeError(f"NH 응답이 JSON이 아님: {response.text}") from exc

        rsp_cd = data.get("rsp_cd")
        rsp_msg = data.get("rsp_msg", "")
        if rsp_cd not in _SUCCESS_CODES and "완료" not in rsp_msg:
            if response.status_code in (401, 403):
                raise FatalExchangeError(f"NH 인증 오류: {data}")
            raise RetryableExchangeError(f"NH API 오류: {data}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


class NHAdapter(
    _NHHTTPClient,
    NHMarketDataMixin,
    NHAccountMixin,
    NHTradingMixin,
    ExchangeAdapter,
):
    """NH투자증권(NH Investment Securities) — NAMUH PLUG OpenAPI, OAuth2 인증.

    ⚠️ 02e 스펙 §0 — NH는 API가 두 세대다: 레거시 QV Open API(Windows
    COM/DLL 기반, REST 아님)와 신규 NAMUH PLUG(진짜 REST+WS). 이 어댑터는
    후자만 구현한다 — 전자는 이 어댑터 구조와 근본적으로 안 맞는다.

    ⚠️ 02e 스펙 §3 — 정정/취소/주문조회 3개 엔드포인트는 공식 예제가
    없어 명명 관례를 연장한 추정치다(라이브 검증 전까지 확정 아님).
    """

    @property
    def is_paper_trading(self) -> bool:
        """2026-09-03 재확인(task-106, PM 배정 지침 (1)) — 이전엔 SDK
        README의 `moapi.nhplug.com` 언급만 믿고 True를 반환했으나, 공식
        API 가이드 포털(nhplug.com)을 직접 열람해보니 접근토큰발급
        엔드포인트의 "모의투자 도메인" 항목이 명시적으로 "미제공"이었다
        (02e 스펙 §0-1). SDK README와 공식 포털이 서로 다른 얘기를 하는
        상태 — "확인될 때만 True" 원칙에 따라 확정되기 전까지 False로
        고정한다. 생성자의 `is_paper_trading` 인자는 여전히 REST 호스트
        선택(실전/모의 URL)에는 쓰이지만, 그 값이 "안전한 샌드박스"를
        보증하지는 않는다 — Executor의 PAPER 전용 게이트가 이 adapter를
        항상 차단하는 게 의도된 안전 방향의 결과다."""
        return False

    @property
    def is_sandboxed(self) -> bool:
        """`is_paper_trading`과 동일 근거(위 참조)."""
        return False

    def get_capabilities(self) -> ExchangeCapability:
        """02e 스펙 §5 — Phase 1은 국내주식(krstock)만, 해외주식/파생상품/
        채권/금현물은 P2로 명시적 스콥 밖(06번 §6.1-A capability-gated
        원칙)."""
        return ExchangeCapability(
            exchange_name="nh",
            supported_asset_classes=[AssetClass.KR_EQUITY],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            # 접속/구독 메시지 형식은 확인했지만(02e 스펙 §4) 실제 데이터
            # 메시지의 응답 포맷을 확인 못해 subscribe_ticker_stream()이
            # 아직 NotImplementedError를 던진다 — 여기서 True를 선언하면
            # "지원한다"는 거짓 신호가 된다(KIS가 겪었던 것과 동일한 실수,
            # PM 배정 지침 (1)과 같은 원칙: 확인 안 되면 False).
            supports_websocket=False,
            max_leverage=Decimal("1"),
            reference_feed_coverage="medium",
            has_official_sandbox=True,
            market_hours=MarketHours(
                timezone="Asia/Seoul",
                open_time="09:00",
                close_time="15:30",
                trading_days=["MON", "TUE", "WED", "THU", "FRI"],
            ),
        )
