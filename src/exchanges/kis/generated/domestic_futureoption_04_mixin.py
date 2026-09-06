"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_futureoption 미착수 TR 청크 04.

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


class KISGeneratedDomesticFutureoption04Mixin(_KISWsHost):

    async def krx_ngt_futures_ccnl_h0mfcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > KRX야간선물 실시간종목체결 [실시간-064] -- WS
        tr_id=H0MFCNT0. 필수 파라미터: tr_key. 응답 필드: futs_shrn_iscd, bsop_hour,
        futs_prdy_vrss, prdy_vrss_sign, futs_prdy_ctrt, futs_prpr, futs_oprc, futs_hgpr,
        futs_lwpr, last_cnqn, acml_vol, acml_tr_pbmn, hts_thpr, mrkt_basis, dprt,
        nmsc_fctn_stpl_prc, fmsc_fctn_stpl_prc, spead_prc, hts_otst_stpl_qty,
        otst_stpl_qty_icdc, oprc_hour, oprc_vrss_prpr_sign, oprc_vrss_nmix_prpr, hgpr_hour,
        hgpr_vrss_prpr_sign, hgpr_vrss_nmix_prpr, lwpr_hour, lwpr_vrss_prpr_sign,
        lwpr_vrss_nmix_prpr, shnu_rate, cttr, esdg, otst_stpl_rgbf_qty_icdc, thpr_basis,
        futs_askp1, futs_bidp1, askp_rsqn1, bidp_rsqn1, seln_cntg_csnu, shnu_cntg_csnu,
        ntby_cntg_csnu, seln_cntg_smtn, shnu_cntg_smtn, total_askp_rsqn, total_bidp_rsqn,
        prdy_vol_vrss_acml_vol_rate, dynm_mxpr, dynm_llam, dynm_prc_limt_yn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0MFCNT0", tr_key
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

    async def futures_exp_ccnl_h0zfanc0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 주식선물 실시간예상체결 [실시간-031] -- WS tr_id=H0ZFANC0.
        필수 파라미터: tr_key. 응답 필드: futs_shrn_iscd, bsop_hour, antc_cnpr, antc_cntg_vrss,
        antc_cntg_vrss_sign, antc_cntg_prdy_ctrt, antc_mkop_cls_code, antc_cnqn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0ZFANC0", tr_key
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

    async def stock_futures_realtime_quote_h0zfasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 주식선물 실시간호가 [실시간-030] -- WS tr_id=H0ZFASP0. 필수
        파라미터: tr_key. 응답 필드: futs_shrn_iscd, bsop_hour, askp1, askp2, askp3, askp4,
        askp5, askp6, askp7, askp8, askp9, askp10, bidp1, bidp2, bidp3, bidp4, bidp5, bidp6,
        bidp7, bidp8, bidp9, bidp10, askp_csnu1, askp_csnu2, askp_csnu3, askp_csnu4, askp_csnu5,
        askp_csnu6, askp_csnu7, askp_csnu8, askp_csnu9, askp_csnu10, bidp_csnu1, bidp_csnu2,
        bidp_csnu3, bidp_csnu4, bidp_csnu5, bidp_csnu6, bidp_csnu7, bidp_csnu8, bidp_csnu9,
        bidp_csnu10, askp_rsqn1, askp_rsqn2, askp_rsqn3, askp_rsqn4, askp_rsqn5, askp_rsqn6,
        askp_rsqn7, askp_rsqn8, askp_rsqn9, askp_rsqn10, bidp_rsqn1, bidp_rsqn2, bidp_rsqn3,
        bidp_rsqn4, bidp_rsqn5, bidp_rsqn6, bidp_rsqn7, bidp_rsqn8, bidp_rsqn9, bidp_rsqn10,
        total_askp_csnu, total_bidp_csnu, total_askp_rsqn, total_bidp_rsqn,
        total_askp_rsqn_icdc, total_bidp_rsqn_icdc.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0ZFASP0", tr_key
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

    async def stock_futures_realtime_conclusion_h0zfcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 주식선물 실시간체결가 [실시간-029] -- WS tr_id=H0ZFCNT0.
        필수 파라미터: tr_key. 응답 필드: futs_shrn_iscd, bsop_hour, stck_prpr, prdy_vrss_sign,
        prdy_vrss, futs_prdy_ctrt, stck_oprc, stck_hgpr, stck_lwpr, last_cnqn, acml_vol,
        acml_tr_pbmn, hts_thpr, mrkt_basis, dprt, nmsc_fctn_stpl_prc, fmsc_fctn_stpl_prc,
        spead_prc, hts_otst_stpl_qty, otst_stpl_qty_icdc, oprc_hour, oprc_vrss_prpr_sign,
        oprc_vrss_prpr, hgpr_hour, hgpr_vrss_prpr_sign, hgpr_vrss_prpr, lwpr_hour,
        lwpr_vrss_prpr_sign, lwpr_vrss_prpr, shnu_rate, cttr, esdg, otst_stpl_rgbf_qty_icdc,
        thpr_basis, askp1, bidp1, askp_rsqn1, bidp_rsqn1, seln_cntg_csnu, shnu_cntg_csnu,
        ntby_cntg_csnu, seln_cntg_smtn, shnu_cntg_smtn, total_askp_rsqn, total_bidp_rsqn,
        prdy_vol_vrss_acml_vol_rate, dynm_mxpr, dynm_llam, dynm_prc_limt_yn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0ZFCNT0", tr_key
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

    async def option_exp_ccnl_h0zoanc0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 주식옵션 실시간예상체결 [실시간-046] -- WS tr_id=H0ZOANC0.
        필수 파라미터: tr_key. 응답 필드: optn_shrn_iscd, bsop_hour, antc_cnpr, antc_cntg_vrss,
        antc_cntg_vrss_sign, antc_cntg_prdy_ctrt, antc_mkop_cls_code.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0ZOANC0", tr_key
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

    async def stock_option_asking_price_h0zoasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 실시간시세 > 주식옵션 실시간호가 [실시간-045] -- WS tr_id=H0ZOASP0. 필수
        파라미터: tr_key. 응답 필드: optn_shrn_iscd, bsop_hour, optn_askp1, optn_askp2,
        optn_askp3, optn_askp4, optn_askp5, optn_bidp1, optn_bidp2, optn_bidp3, optn_bidp4,
        optn_bidp5, askp_csnu1, askp_csnu2, askp_csnu3, askp_csnu4, askp_csnu5, bidp_csnu1,
        bidp_csnu2, bidp_csnu3, bidp_csnu4, bidp_csnu5, askp_rsqn1, askp_rsqn2, askp_rsqn3,
        askp_rsqn4, askp_rsqn5, bidp_rsqn1, bidp_rsqn2, bidp_rsqn3, bidp_rsqn4, bidp_rsqn5,
        total_askp_csnu, total_bidp_csnu, total_askp_rsqn, total_bidp_rsqn,
        total_askp_rsqn_icdc, total_bidp_rsqn_icdc, optn_askp6, optn_askp7, optn_askp8,
        optn_askp9, optn_askp10, optn_bidp6, optn_bidp7, optn_bidp8, optn_bidp9, optn_bidp10,
        askp_csnu6, askp_csnu7, askp_csnu8, askp_csnu9, askp_csnu10, bidp_csnu6, bidp_csnu7,
        bidp_csnu8, bidp_csnu9, bidp_csnu10, askp_rsqn6, askp_rsqn7, askp_rsqn8, askp_rsqn9,
        askp_rsqn10, bidp_rsqn6, bidp_rsqn7, bidp_rsqn8, bidp_rsqn9, bidp_rsqn10.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0ZOASP0", tr_key
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
