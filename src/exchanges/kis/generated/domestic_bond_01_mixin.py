"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_bond 미착수 TR 청크 01.

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


class KISGeneratedDomesticBond01Mixin(_KISRestHost, _KISWsHost):

    async def issue_info_ctpf1101r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권 발행정보 -- GET
        /uapi/domestic-bond/v1/quotations/issue-info (tr_id=CTPF1101R). 필수: PDNO,
        PRDT_TYPE_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/issue-info",
            "CTPF1101R",
            params=params or {},
        )

    async def search_bond_info_ctpf1114r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권 기본조회 -- GET
        /uapi/domestic-bond/v1/quotations/search-bond-info (tr_id=CTPF1114R). 필수: PDNO,
        PRDT_TYPE_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/search-bond-info",
            "CTPF1114R",
            params=params or {},
        )

    async def avg_unit_ctpf2005r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권 평균단가조회 -- GET
        /uapi/domestic-bond/v1/quotations/avg-unit (tr_id=CTPF2005R). 필수: INQR_STRT_DT,
        INQR_END_DT, PDNO, PRDT_TYPE_CD, VRFC_KIND_CD. 선택: CTX_AREA_NK30, CTX_AREA_FK100. 응답
        컨테이너: output1, output2, output3.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/avg-unit",
            "CTPF2005R",
            params=params or {},
        )

    async def inquire_daily_ccld_ctsc8013r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 주문/계좌 - 장내채권 주문체결내역 -- GET
        /uapi/domestic-bond/v1/trading/inquire-daily-ccld (tr_id=CTSC8013R). 필수: CANO,
        ACNT_PRDT_CD, INQR_STRT_DT, INQR_END_DT, SLL_BUY_DVSN_CD, SORT_SQN_DVSN, PDNO, NCCS_YN,
        CTX_AREA_NK200, CTX_AREA_FK200. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/trading/inquire-daily-ccld",
            "CTSC8013R",
            params=params or {},
        )

    async def inquire_psbl_rvsecncl_ctsc8035r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 주문/계좌 - 채권정정취소가능주문조회 -- GET
        /uapi/domestic-bond/v1/trading/inquire-psbl-rvsecncl (tr_id=CTSC8035R). 필수: CANO,
        ACNT_PRDT_CD, ORD_DT, ODNO, CTX_AREA_FK200, CTX_AREA_NK200. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/trading/inquire-psbl-rvsecncl",
            "CTSC8035R",
            params=params or {},
        )

    async def inquire_asking_price_fhkbj773401c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권현재가(호가) -- GET
        /uapi/domestic-bond/v1/quotations/inquire-asking-price (tr_id=FHKBJ773401C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/inquire-asking-price",
            "FHKBJ773401C0",
            params=params or {},
        )

    async def inquire_ccnl_fhkbj773403c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권현재가(체결) -- GET
        /uapi/domestic-bond/v1/quotations/inquire-ccnl (tr_id=FHKBJ773403C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/inquire-ccnl",
            "FHKBJ773403C0",
            params=params or {},
        )

    async def inquire_daily_price_fhkbj773404c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권현재가(일별) -- GET
        /uapi/domestic-bond/v1/quotations/inquire-daily-price (tr_id=FHKBJ773404C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/inquire-daily-price",
            "FHKBJ773404C0",
            params=params or {},
        )

    async def inquire_daily_itemchartprice_fhkbj773701c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 기본시세 - 장내채권 기간별시세(일) -- GET
        /uapi/domestic-bond/v1/quotations/inquire-daily-itemchartprice (tr_id=FHKBJ773701C0).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-bond/v1/quotations/inquire-daily-itemchartprice",
            "FHKBJ773701C0",
            params=params or {},
        )

    async def bond_index_ccnl_h0bicnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 실시간시세 > 채권지수 실시간체결가 [실시간-060] -- WS tr_id=H0BICNT0. 필수
        파라미터: tr_key. 응답 필드: nmix_id, stnd_date1, trnm_hour, totl_ernn_nmix_oprc,
        totl_ernn_nmix_hgpr, totl_ernn_nmix_lwpr, totl_ernn_nmix, prdy_totl_ernn_nmix,
        totl_ernn_nmix_prdy_vrss, totl_ernn_nmix_prdy_vrss_sign, totl_ernn_nmix_prdy_ctrt,
        clen_prc_nmix, mrkt_prc_nmix, bond_call_rnvs_nmix, bond_zero_rnvs_nmix, bond_futs_thpr,
        bond_avrg_drtn_val, bond_avrg_cnvx_val, bond_avrg_ytm_val, bond_avrg_frdl_ytm_val.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0BICNT0", tr_key
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

    async def bond_asking_price_h0bjasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 실시간시세 > 일반채권 실시간호가 [실시간-053] -- WS tr_id=H0BJASP0. 필수
        파라미터: tr_key. 응답 필드: stnd_iscd, stck_cntg_hour, askp_ert1, bidp_ert1, askp1,
        bidp1, askp_rsqn1, bidp_rsqn1, askp_ert2, bidp_ert2, askp2, bidp2, askp_rsqn2,
        bidp_rsqn2, askp_ert3, bidp_ert3, askp3, bidp3, askp_rsqn3, bidp_rsqn3, askp_ert4,
        bidp_ert4, askp4, bidp4, askp_rsqn4, bidp_rsqn4, askp_ert5, bidp_ert5, askp5, bidp5,
        askp_rsqn52, bidp_rsqn53, total_askp_rsqn, total_bidp_rsqn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0BJASP0", tr_key
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

    async def bond_ccnl_h0bjcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [장내채권] 실시간시세 > 일반채권 실시간체결가 [실시간-052] -- WS tr_id=H0BJCNT0. 필수
        파라미터: tr_key. 응답 필드: stnd_iscd, bond_isnm, stck_cntg_hour, prdy_vrss_sign,
        prdy_vrss, prdy_ctrt, stck_prpr, cntg_vol, stck_oprc, stck_hgpr, stck_lwpr,
        stck_prdy_clpr, bond_cntg_ert, oprc_ert, hgpr_ert, lwpr_ert, acml_vol, prdy_vol,
        cntg_type_cls_code.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0BJCNT0", tr_key
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
