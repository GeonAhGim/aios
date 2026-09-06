"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_futureoption 미착수 TR 청크 05.

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


class KISGeneratedDomesticFutureoption05Mixin(_KISRestHost, _KISWsHost):

    async def stock_option_ccnl_h0zocnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 주식옵션 실시간체결가 [실시간-044] -- WS tr_id=H0ZOCNT0.
        필수 파라미터: tr_key. 응답 필드: optn_shrn_iscd, bsop_hour, optn_prpr, prdy_vrss_sign,
        optn_prdy_vrss, prdy_ctrt, optn_oprc, optn_hgpr, optn_lwpr, last_cnqn, acml_vol,
        acml_tr_pbmn, hts_thpr, hts_otst_stpl_qty, otst_stpl_qty_icdc, oprc_hour,
        oprc_vrss_prpr_sign, oprc_vrss_nmix_prpr, hgpr_hour, hgpr_vrss_prpr_sign,
        hgpr_vrss_nmix_prpr, lwpr_hour, lwpr_vrss_prpr_sign, lwpr_vrss_nmix_prpr, shnu_rate,
        prmm_val, invl_val, tmvl_val, delta, gama, vega, theta, rho, hts_ints_vltl, esdg,
        otst_stpl_rgbf_qty_icdc, thpr_basis, unas_hist_vltl, cttr, dprt, mrkt_basis, optn_askp1,
        optn_bidp1, askp_rsqn1, bidp_rsqn1, seln_cntg_csnu, shnu_cntg_csnu, ntby_cntg_csnu,
        seln_cntg_smtn, shnu_cntg_smtn, total_askp_rsqn, total_bidp_rsqn,
        prdy_vol_vrss_acml_vol_rate.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0ZOCNT0", tr_key
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

    async def order_sttn1101u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 주문[v1_국내선물-001] -- POST
        /uapi/domestic-futureoption/v1/trading/order (tr_id=STTN1101U). 필수: ORD_PRCS_DVSN_CD,
        CANO, ACNT_PRDT_CD, SLL_BUY_DVSN_CD, SHTN_PDNO, ORD_QTY, UNIT_PRICE, NMPR_TYPE_CD,
        KRX_NMPR_CNDT_CD, ORD_DVSN_CD. 선택: CTAC_TLNO, FUOP_ITEM_DVSN_CD. 응답 컨테이너:
        output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-futureoption/v1/trading/order",
            "STTN1101U",
            body=params or {},
        )

    async def inquire_psbl_ngt_order_sttn5105r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > (야간)선물옵션 주문가능 조회 [국내선물-011] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-psbl-ngt-order (tr_id=STTN5105R). 필수:
        CANO, ACNT_PRDT_CD, PDNO, PRDT_TYPE_CD, SLL_BUY_DVSN_CD, UNIT_PRICE, ORD_DVSN_CD. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-psbl-ngt-order",
            "STTN5105R",
            params=params or {},
        )

    async def inquire_ngt_ccnl_sttn5201r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > (야간)선물옵션 주문체결 내역조회 [국내선물-009] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl (tr_id=STTN5201R). 필수: CANO,
        ACNT_PRDT_CD, STRT_ORD_DT, END_ORD_DT, SLL_BUY_DVSN_CD, CCLD_NCCS_DVSN. 선택: SORT_SQN,
        STRT_ODNO, PDNO, MKET_ID_CD, FUOP_DVSN_CD, SCRN_DVSN, CTX_AREA_FK200, CTX_AREA_NK200.
        응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl",
            "STTN5201R",
            params=params or {},
        )

    async def inquire_psbl_order_ttto5105r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 주문가능[v1_국내선물-005] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-psbl-order (tr_id=TTTO5105R). 필수: CANO,
        ACNT_PRDT_CD, PDNO, SLL_BUY_DVSN_CD, UNIT_PRICE, ORD_DVSN_CD. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-psbl-order",
            "TTTO5105R",
            params=params or {},
        )

    async def inquire_ccnl_ttto5201r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 주문체결내역조회[v1_국내선물-003] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-ccnl (tr_id=TTTO5201R). 필수: CANO,
        ACNT_PRDT_CD, STRT_ORD_DT, END_ORD_DT, SLL_BUY_DVSN_CD, CCLD_NCCS_DVSN, SORT_SQN. 선택:
        PDNO, STRT_ODNO, MKET_ID_CD, CTX_AREA_FK200, CTX_AREA_NK200. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-ccnl",
            "TTTO5201R",
            params=params or {},
        )

    async def order_vtto1101u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 주문[v1_국내선물-001] -- POST
        /uapi/domestic-futureoption/v1/trading/order (tr_id=VTTO1101U). 필수: ORD_PRCS_DVSN_CD,
        CANO, ACNT_PRDT_CD, SLL_BUY_DVSN_CD, SHTN_PDNO, ORD_QTY, UNIT_PRICE, NMPR_TYPE_CD,
        KRX_NMPR_CNDT_CD, ORD_DVSN_CD. 선택: CTAC_TLNO, FUOP_ITEM_DVSN_CD. 응답 컨테이너:
        output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-futureoption/v1/trading/order",
            "VTTO1101U",
            body=params or {},
        )

    async def order_rvsecncl_vtto1103u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 정정취소주문[v1_국내선물-002] -- POST
        /uapi/domestic-futureoption/v1/trading/order-rvsecncl (tr_id=VTTO1103U). 필수:
        ORD_PRCS_DVSN_CD, CANO, ACNT_PRDT_CD, RVSE_CNCL_DVSN_CD, ORGN_ODNO, ORD_QTY, UNIT_PRICE,
        NMPR_TYPE_CD, KRX_NMPR_CNDT_CD, RMN_QTY_YN, ORD_DVSN_CD. 선택: FUOP_ITEM_DVSN_CD. 응답
        컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-futureoption/v1/trading/order-rvsecncl",
            "VTTO1103U",
            body=params or {},
        )

    async def inquire_psbl_order_vtto5105r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 주문가능[v1_국내선물-005] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-psbl-order (tr_id=VTTO5105R). 필수: CANO,
        ACNT_PRDT_CD, PDNO, SLL_BUY_DVSN_CD, UNIT_PRICE, ORD_DVSN_CD. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-psbl-order",
            "VTTO5105R",
            params=params or {},
        )

    async def inquire_ccnl_vtto5201r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 주문체결내역조회[v1_국내선물-003] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-ccnl (tr_id=VTTO5201R). 필수: CANO,
        ACNT_PRDT_CD, STRT_ORD_DT, END_ORD_DT, SLL_BUY_DVSN_CD, CCLD_NCCS_DVSN, SORT_SQN. 선택:
        PDNO, STRT_ODNO, MKET_ID_CD, CTX_AREA_FK200, CTX_AREA_NK200. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-ccnl",
            "VTTO5201R",
            params=params or {},
        )
