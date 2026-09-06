"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_futureoption 미착수 TR 청크 03.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from src.exchanges.kis.generated._protocols import _KISWsHost
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


class KISGeneratedDomesticFutureoption03Mixin(_KISWsHost):

    async def fuopt_ccnl_notice_h0ifcni0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 선물옵션 실시간체결통보[실시간-012] -- WS tr_id=H0IFCNI0.
        필수 파라미터: tr_key. 응답 필드: cust_id, acnt_no, oder_no, ooder_no, seln_byov_cls,
        rctf_cls, oder_kind2, stck_shrn_iscd, cntg_qty, cntg_unpr, stck_cntg_hour, rfus_yn,
        cntg_yn, acpt_yn, brnc_no, oder_qty, acnt_name, cntg_isnm, oder_cond, ord_grp,
        ord_grpseq, order_prc.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0IFCNI0", tr_key
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

    async def index_futures_realtime_conclusion_h0ifcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 지수선물 실시간체결가[실시간-010] -- WS tr_id=H0IFCNT0. 필수
        파라미터: tr_key. 응답 필드: futs_shrn_iscd, bsop_hour, futs_prdy_vrss, prdy_vrss_sign,
        futs_prdy_ctrt, futs_prpr, futs_oprc, futs_hgpr, futs_lwpr, last_cnqn, acml_vol,
        acml_tr_pbmn, hts_thpr, mrkt_basis, dprt, nmsc_fctn_stpl_prc, fmsc_fctn_stpl_prc,
        spead_prc, hts_otst_stpl_qty, otst_stpl_qty_icdc, oprc_hour, oprc_vrss_prpr_sign,
        oprc_vrss_nmix_prpr, hgpr_hour, hgpr_vrss_prpr_sign, hgpr_vrss_nmix_prpr, lwpr_hour,
        lwpr_vrss_prpr_sign, lwpr_vrss_nmix_prpr, shnu_rate, cttr, esdg,
        otst_stpl_rgbf_qty_icdc, thpr_basis, futs_askp1, futs_bidp1, askp_rsqn1, bidp_rsqn1,
        seln_cntg_csnu, shnu_cntg_csnu, ntby_cntg_csnu, seln_cntg_smtn, shnu_cntg_smtn,
        total_askp_rsqn, total_bidp_rsqn, prdy_vol_vrss_acml_vol_rate, dscs_bltr_acml_qty,
        dynm_mxpr, dynm_llam, dynm_prc_limt_yn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0IFCNT0", tr_key
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

    async def index_option_realtime_quote_h0ioasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 지수옵션 실시간호가[실시간-015] -- WS tr_id=H0IOASP0. 필수
        파라미터: tr_key. 응답 필드: optn_shrn_iscd, bsop_hour, optn_askp1, optn_askp2,
        optn_askp3, optn_askp4, optn_askp5, optn_bidp1, optn_bidp2, optn_bidp3, optn_bidp4,
        optn_bidp5, askp_csnu1, askp_csnu2, askp_csnu3, askp_csnu4, askp_csnu5, bidp_csnu1,
        bidp_csnu2, bidp_csnu3, bidp_csnu4, bidp_csnu5, askp_rsqn1, askp_rsqn2, askp_rsqn3,
        askp_rsqn4, askp_rsqn5, bidp_rsqn1, bidp_rsqn2, bidp_rsqn3, bidp_rsqn4, bidp_rsqn5,
        total_askp_csnu, total_bidp_csnu, total_askp_rsqn, total_bidp_rsqn,
        total_askp_rsqn_icdc, total_bidp_rsqn_icdc.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0IOASP0", tr_key
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

    async def index_option_realtime_conclusion_h0iocnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 지수옵션 실시간체결가[실시간-014] -- WS tr_id=H0IOCNT0. 필수
        파라미터: tr_key. 응답 필드: optn_shrn_iscd, bsop_hour, optn_prpr, prdy_vrss_sign,
        optn_prdy_vrss, prdy_ctrt, optn_oprc, optn_hgpr, optn_lwpr, last_cnqn, acml_vol,
        acml_tr_pbmn, hts_thpr, hts_otst_stpl_qty, otst_stpl_qty_icdc, oprc_hour,
        oprc_vrss_prpr_sign, oprc_vrss_nmix_prpr, hgpr_hour, hgpr_vrss_prpr_sign,
        hgpr_vrss_nmix_prpr, lwpr_hour, lwpr_vrss_prpr_sign, lwpr_vrss_nmix_prpr, shnu_rate,
        prmm_val, invl_val, tmvl_val, delta, gama, vega, theta, rho, hts_ints_vltl, esdg,
        otst_stpl_rgbf_qty_icdc, thpr_basis, unas_hist_vltl, cttr, dprt, mrkt_basis, optn_askp1,
        optn_bidp1, askp_rsqn1, bidp_rsqn1, seln_cntg_csnu, shnu_cntg_csnu, ntby_cntg_csnu,
        seln_cntg_smtn, shnu_cntg_smtn, total_askp_rsqn, total_bidp_rsqn,
        prdy_vol_vrss_acml_vol_rate, avrg_vltl, dscs_lrqn_vol, dynm_mxpr, dynm_llam,
        dynm_prc_limt_yn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0IOCNT0", tr_key
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

    async def krx_ngt_futures_asking_price_h0mfasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > KRX야간선물 실시간호가 [실시간-065] -- WS tr_id=H0MFASP0.
        필수 파라미터: tr_key. 응답 필드: futs_shrn_iscd, bsop_hour, futs_askp1, futs_askp2,
        futs_askp3, futs_askp4, futs_askp5, futs_bidp1, futs_bidp2, futs_bidp3, futs_bidp4,
        futs_bidp5, askp_csnu1, askp_csnu2, askp_csnu3, askp_csnu4, askp_csnu5, bidp_csnu1,
        bidp_csnu2, bidp_csnu3, bidp_csnu4, bidp_csnu5, askp_rsqn1, askp_rsqn2, askp_rsqn3,
        askp_rsqn4, askp_rsqn5, bidp_rsqn1, bidp_rsqn2, bidp_rsqn3, bidp_rsqn4, bidp_rsqn5,
        total_askp_csnu, total_bidp_csnu, total_askp_rsqn, total_bidp_rsqn,
        total_askp_rsqn_icdc, total_bidp_rsqn_icdc.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0MFASP0", tr_key
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

    async def krx_ngt_futures_ccnl_notice_h0mfcni0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > KRX야간선물 실시간체결통보 [실시간-066] -- WS
        tr_id=H0MFCNI0. 필수 파라미터: tr_key. 응답 필드: cust_id, acnt_no, oder_no, ooder_no,
        seln_byov_cls, rctf_cls, oder_kind2, stck_shrn_iscd, cntg_qty, cntg_unpr,
        stck_cntg_hour, rfus_yn, cntg_yn, acpt_yn, brnc_no, oder_qty, acnt_name, cntg_isnm,
        oder_cond.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0MFCNI0", tr_key
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
