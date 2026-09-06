"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_stock 미착수 TR 청크 02.

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


class KISGeneratedOverseasStock02Mixin(_KISRestHost, _KISWsHost):

    async def delayed_asking_price_asia_hdfsasp1(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 실시간시세 > 해외주식 지연호가(아시아)[실시간-008] -- WS tr_id=HDFSASP1. 필수
        파라미터: tr_key. 응답 필드: symb, zdiv, xymd, xhms, kymd, khms, bvol, avol, bdvl, advl,
        pbid1, pask1, vbid1, vask1, dbid1, dask1.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "HDFSASP1", tr_key
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

    async def inquire_asking_price_hhdfs76200100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 현재가 1호가[해외주식-033] -- GET
        /uapi/overseas-price/v1/quotations/inquire-asking-price (tr_id=HHDFS76200100). 필수:
        AUTH, EXCD, SYMB. 선택: 없음. 응답 컨테이너: output1, output2, output3.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/inquire-asking-price",
            "HHDFS76200100",
            params=params or {},
        )

    async def price_detail_hhdfs76200200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 현재가상세[v1_해외주식-029] -- GET
        /uapi/overseas-price/v1/quotations/price-detail (tr_id=HHDFS76200200). 필수: AUTH, EXCD,
        SYMB. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200",
            params=params or {},
        )

    async def quot_inquire_ccnl_hhdfs76200300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 체결추이[해외주식-037] -- GET
        /uapi/overseas-price/v1/quotations/inquire-ccnl (tr_id=HHDFS76200300). 필수: EXCD, TDAY,
        SYMB. 선택: AUTH, KEYB. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/inquire-ccnl",
            "HHDFS76200300",
            params=params or {},
        )

    async def dailyprice_hhdfs76240000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 기간별시세[v1_해외주식-010] -- GET
        /uapi/overseas-price/v1/quotations/dailyprice (tr_id=HHDFS76240000). 필수: AUTH, EXCD,
        SYMB, GUBN, BYMD, MODP. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/dailyprice",
            "HHDFS76240000",
            params=params or {},
        )

    async def price_fluct_hhdfs76260000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 가격급등락[해외주식-038] -- GET
        /uapi/overseas-stock/v1/ranking/price-fluct (tr_id=HHDFS76260000). 필수: EXCD, GUBN,
        MINX, VOL_RANG. 선택: KEYB, AUTH. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/price-fluct",
            "HHDFS76260000",
            params=params or {},
        )

    async def volume_surge_hhdfs76270000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 거래량급증[해외주식-039] -- GET
        /uapi/overseas-stock/v1/ranking/volume-surge (tr_id=HHDFS76270000). 필수: EXCD, MINX,
        VOL_RANG. 선택: KEYB, AUTH. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/volume-surge",
            "HHDFS76270000",
            params=params or {},
        )

    async def volume_power_hhdfs76280000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 매수체결강도상위[해외주식-040] -- GET
        /uapi/overseas-stock/v1/ranking/volume-power (tr_id=HHDFS76280000). 필수: EXCD, NDAY,
        VOL_RANG. 선택: AUTH, KEYB. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/volume-power",
            "HHDFS76280000",
            params=params or {},
        )

    async def updown_rate_hhdfs76290000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 상승률/하락률[해외주식-041] -- GET
        /uapi/overseas-stock/v1/ranking/updown-rate (tr_id=HHDFS76290000). 필수: EXCD, NDAY,
        GUBN, VOL_RANG. 선택: AUTH, KEYB. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/updown-rate",
            "HHDFS76290000",
            params=params or {},
        )

    async def new_highlow_hhdfs76300000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 신고/신저가[해외주식-042] -- GET
        /uapi/overseas-stock/v1/ranking/new-highlow (tr_id=HHDFS76300000). 필수: EXCD, MINX,
        VOL_RANG, GUBN, GUBN2. 선택: KEYB, AUTH. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/new-highlow",
            "HHDFS76300000",
            params=params or {},
        )

    async def trade_vol_hhdfs76310010(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 거래량순위[해외주식-043] -- GET
        /uapi/overseas-stock/v1/ranking/trade-vol (tr_id=HHDFS76310010). 필수: EXCD, NDAY,
        VOL_RANG. 선택: KEYB, AUTH, PRC1, PRC2. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/trade-vol",
            "HHDFS76310010",
            params=params or {},
        )

    async def trade_pbmn_hhdfs76320010(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 거래대금순위[해외주식-044] -- GET
        /uapi/overseas-stock/v1/ranking/trade-pbmn (tr_id=HHDFS76320010). 필수: EXCD, NDAY,
        VOL_RANG. 선택: AUTH, KEYB, PRC1, PRC2. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/trade-pbmn",
            "HHDFS76320010",
            params=params or {},
        )

    async def trade_growth_hhdfs76330000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 거래증가율순위[해외주식-045] -- GET
        /uapi/overseas-stock/v1/ranking/trade-growth (tr_id=HHDFS76330000). 필수: EXCD, NDAY,
        VOL_RANG. 선택: AUTH, KEYB. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/trade-growth",
            "HHDFS76330000",
            params=params or {},
        )

    async def trade_turnover_hhdfs76340000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 거래회전율순위[해외주식-046] -- GET
        /uapi/overseas-stock/v1/ranking/trade-turnover (tr_id=HHDFS76340000). 필수: EXCD, NDAY,
        VOL_RANG. 선택: KEYB, AUTH. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/trade-turnover",
            "HHDFS76340000",
            params=params or {},
        )

    async def market_cap_hhdfs76350100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 시가총액순위[해외주식-047] -- GET
        /uapi/overseas-stock/v1/ranking/market-cap (tr_id=HHDFS76350100). 필수: EXCD, VOL_RANG.
        선택: KEYB, AUTH. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/ranking/market-cap",
            "HHDFS76350100",
            params=params or {},
        )

    async def industry_theme_hhdfs76370000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 업종별시세[해외주식-048] -- GET
        /uapi/overseas-price/v1/quotations/industry-theme (tr_id=HHDFS76370000). 필수: EXCD,
        ICOD, VOL_RANG. 선택: AUTH, KEYB. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/industry-theme",
            "HHDFS76370000",
            params=params or {},
        )
