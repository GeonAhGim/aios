"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- etfetn 미착수 TR 청크 01.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost, _KISWsHost
from src.exchanges.kis.websocket_connection import (
    ConnectFn,
    MessageHandler,
    ReconnectHook,
    _connect,
    _run_kis_ws_subscription,
)
from src.exchanges.kis.websocket_mixin import (
    WS_PAPER_URL,
    WS_REAL_URL,
)
from src.exchanges.kis.websocket_parsing import _build_subscribe_message


class KISGeneratedEtfetn01Mixin(_KISRestHost, _KISWsHost):

    async def inquire_component_stock_price_fhkst121600c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > ETF 구성종목시세[국내주식-073] -- GET
        /uapi/etfetn/v1/quotations/inquire-component-stock-price (tr_id=FHKST121600C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_COND_SCR_DIV_CODE. 선택: 없음. 응답
        컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/etfetn/v1/quotations/inquire-component-stock-price",
            "FHKST121600C0",
            params=params or {},
        )

    async def nav_comparison_trend_fhpst02440000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > NAV 비교추이(종목)[v1_국내주식-069] -- GET
        /uapi/etfetn/v1/quotations/nav-comparison-trend (tr_id=FHPST02440000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/etfetn/v1/quotations/nav-comparison-trend",
            "FHPST02440000",
            params=params or {},
        )

    async def nav_comparison_time_trend_fhpst02440100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > NAV 비교추이(분)[v1_국내주식-070] -- GET
        /uapi/etfetn/v1/quotations/nav-comparison-time-trend (tr_id=FHPST02440100). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_HOUR_CLS_CODE. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/etfetn/v1/quotations/nav-comparison-time-trend",
            "FHPST02440100",
            params=params or {},
        )

    async def nav_comparison_daily_trend_fhpst02440200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > NAV 비교추이(일)[v1_국내주식-071] -- GET
        /uapi/etfetn/v1/quotations/nav-comparison-daily-trend (tr_id=FHPST02440200). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1, FID_INPUT_DATE_2. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/etfetn/v1/quotations/nav-comparison-daily-trend",
            "FHPST02440200",
            params=params or {},
        )

    async def etf_nav_trend_h0stnav0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내ETF NAV추이[실시간-051] -- WS tr_id=H0STNAV0. 필수 파라미터:
        tr_key. 응답 필드: rt_cd, msg_cd, output1, msg1, mksc_shrn_iscd, nav,
        nav_prdy_vrss_sign, nav_prdy_vrss, nav_prdy_ctrt, oprc_nav, hprc_nav, lprc_nav.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0STNAV0", tr_key
        )

        async def _on_frame(raw: str) -> None:
            await callback(raw)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            _on_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )
