"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 06.

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


class KISGeneratedDomesticStock06Mixin(_KISWsHost):

    async def exp_ccnl_nxt_h0nxanc0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 실시간예상체결 (NXT) -- WS tr_id=H0NXANC0. 필수
        파라미터: tr_key. 응답 필드: MKSC_SHRN_ISCD, STCK_CNTG_HOUR, STCK_PRPR, PRDY_VRSS_SIGN,
        PRDY_VRSS, PRDY_CTRT, WGHN_AVRG_STCK_PRC, STCK_OPRC, STCK_HGPR, STCK_LWPR, ASKP1, BIDP1,
        CNTG_VOL, ACML_VOL, ACML_TR_PBMN, SELN_CNTG_CSNU, SHNU_CNTG_CSNU, NTBY_CNTG_CSNU, CTTR,
        SELN_CNTG_SMTN, SHNU_CNTG_SMTN, CNTG_CLS_CODE, SHNU_RATE, PRDY_VOL_VRSS_ACML_VOL_RATE,
        OPRC_HOUR, OPRC_VRSS_PRPR_SIGN, OPRC_VRSS_PRPR, HGPR_HOUR, HGPR_VRSS_PRPR_SIGN,
        HGPR_VRSS_PRPR, LWPR_HOUR, LWPR_VRSS_PRPR_SIGN, LWPR_VRSS_PRPR, BSOP_DATE,
        NEW_MKOP_CLS_CODE, TRHT_YN, ASKP_RSQN1, BIDP_RSQN1, TOTAL_ASKP_RSQN, TOTAL_BIDP_RSQN,
        VOL_TNRT, PRDY_SMNS_HOUR_ACML_VOL, PRDY_SMNS_HOUR_ACML_VOL_RATE, HOUR_CLS_CODE,
        MRKT_TRTM_CLS_CODE, VI_STND_PRC.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0NXANC0", tr_key
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

    async def asking_price_nxt_h0nxasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 실시간호가 (NXT) -- WS tr_id=H0NXASP0. 필수 파라미터:
        tr_key. 응답 필드: MKSC_SHRN_ISCD, BSOP_HOUR, HOUR_CLS_CODE, ASKP1, ASKP2, ASKP3, ASKP4,
        ASKP5, ASKP6, ASKP7, ASKP8, ASKP9, ASKP10, BIDP1, BIDP2, BIDP3, BIDP4, BIDP5, BIDP6,
        BIDP7, BIDP8, BIDP9, BIDP10, ASKP_RSQN1, ASKP_RSQN2, ASKP_RSQN3, ASKP_RSQN4, ASKP_RSQN5,
        ASKP_RSQN6, ASKP_RSQN7, ASKP_RSQN8, ASKP_RSQN9, ASKP_RSQN10, BIDP_RSQN1, BIDP_RSQN2,
        BIDP_RSQN3, BIDP_RSQN4, BIDP_RSQN5, BIDP_RSQN6, BIDP_RSQN7, BIDP_RSQN8, BIDP_RSQN9,
        BIDP_RSQN10, TOTAL_ASKP_RSQN, TOTAL_BIDP_RSQN, OVTM_TOTAL_ASKP_RSQN,
        OVTM_TOTAL_BIDP_RSQN, ANTC_CNPR, ANTC_CNQN, ANTC_VOL, ANTC_CNTG_VRSS,
        ANTC_CNTG_VRSS_SIGN, ANTC_CNTG_PRDY_CTRT, ACML_VOL, TOTAL_ASKP_RSQN_ICDC,
        TOTAL_BIDP_RSQN_ICDC, OVTM_TOTAL_ASKP_ICDC, OVTM_TOTAL_BIDP_ICDC, STCK_DEAL_CLS_CODE,
        KMID_PRC, KMID_TOTAL_RSQN, KMID_CLS_CODE, NMID_PRC, NMID_TOTAL_RSQN, NMID_CLS_CODE.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0NXASP0", tr_key
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

    async def ccnl_nxt_h0nxcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 실시간체결가 (NXT) -- WS tr_id=H0NXCNT0. 필수 파라미터:
        tr_key. 응답 필드: MKSC_SHRN_ISCD, STCK_CNTG_HOUR, STCK_PRPR, PRDY_VRSS_SIGN, PRDY_VRSS,
        PRDY_CTRT, WGHN_AVRG_STCK_PRC, STCK_OPRC, STCK_HGPR, STCK_LWPR, ASKP1, BIDP1, CNTG_VOL,
        ACML_VOL, ACML_TR_PBMN, SELN_CNTG_CSNU, SHNU_CNTG_CSNU, NTBY_CNTG_CSNU, CTTR,
        SELN_CNTG_SMTN, SHNU_CNTG_SMTN, CNTG_CLS_CODE, SHNU_RATE, PRDY_VOL_VRSS_ACML_VOL_RATE,
        OPRC_HOUR, OPRC_VRSS_PRPR_SIGN, OPRC_VRSS_PRPR, HGPR_HOUR, HGPR_VRSS_PRPR_SIGN,
        HGPR_VRSS_PRPR, LWPR_HOUR, LWPR_VRSS_PRPR_SIGN, LWPR_VRSS_PRPR, BSOP_DATE,
        NEW_MKOP_CLS_CODE, TRHT_YN, ASKP_RSQN1, BIDP_RSQN1, TOTAL_ASKP_RSQN, TOTAL_BIDP_RSQN,
        VOL_TNRT, PRDY_SMNS_HOUR_ACML_VOL, PRDY_SMNS_HOUR_ACML_VOL_RATE, HOUR_CLS_CODE,
        MRKT_TRTM_CLS_CODE, VI_STND_PRC.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0NXCNT0", tr_key
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

    async def member_nxt_h0nxmbc0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 실시간회원사 (NXT) -- WS tr_id=H0NXMBC0. 필수 파라미터:
        tr_key. 응답 필드: MKSC_SHRN_ISCD, SELN2_MBCR_NAME1, SELN2_MBCR_NAME2, SELN2_MBCR_NAME3,
        SELN2_MBCR_NAME4, SELN2_MBCR_NAME5, BYOV_MBCR_NAME1, BYOV_MBCR_NAME2, BYOV_MBCR_NAME3,
        BYOV_MBCR_NAME4, BYOV_MBCR_NAME5, TOTAL_SELN_QTY1, TOTAL_SELN_QTY2, TOTAL_SELN_QTY3,
        TOTAL_SELN_QTY4, TOTAL_SELN_QTY5, TOTAL_SHNU_QTY1, TOTAL_SHNU_QTY2, TOTAL_SHNU_QTY3,
        TOTAL_SHNU_QTY4, TOTAL_SHNU_QTY5, SELN_MBCR_GLOB_YN_1, SELN_MBCR_GLOB_YN_2,
        SELN_MBCR_GLOB_YN_3, SELN_MBCR_GLOB_YN_4, SELN_MBCR_GLOB_YN_5, SHNU_MBCR_GLOB_YN_1,
        SHNU_MBCR_GLOB_YN_2, SHNU_MBCR_GLOB_YN_3, SHNU_MBCR_GLOB_YN_4, SHNU_MBCR_GLOB_YN_5,
        SELN_MBCR_NO1, SELN_MBCR_NO2, SELN_MBCR_NO3, SELN_MBCR_NO4, SELN_MBCR_NO5,
        SHNU_MBCR_NO1, SHNU_MBCR_NO2, SHNU_MBCR_NO3, SHNU_MBCR_NO4, SHNU_MBCR_NO5,
        SELN_MBCR_RLIM1, SELN_MBCR_RLIM2, SELN_MBCR_RLIM3, SELN_MBCR_RLIM4, SELN_MBCR_RLIM5,
        SHNU_MBCR_RLIM1, SHNU_MBCR_RLIM2, SHNU_MBCR_RLIM3, SHNU_MBCR_RLIM4, SHNU_MBCR_RLIM5,
        SELN_QTY_ICDC1, SELN_QTY_ICDC2, SELN_QTY_ICDC3, SELN_QTY_ICDC4, SELN_QTY_ICDC5,
        SHNU_QTY_ICDC1, SHNU_QTY_ICDC2, SHNU_QTY_ICDC3, SHNU_QTY_ICDC4, SHNU_QTY_ICDC5,
        GLOB_TOTAL_SELN_QTY, GLOB_TOTAL_SHNU_QTY, GLOB_TOTAL_SELN_QTY_ICDC,
        GLOB_TOTAL_SHNU_QTY_ICDC, GLOB_NTBY_QTY, GLOB_SELN_RLIM, GLOB_SHNU_RLIM,
        SELN2_MBCR_ENG_NAME1, SELN2_MBCR_ENG_NAME2, SELN2_MBCR_ENG_NAME3, SELN2_MBCR_ENG_NAME4,
        SELN2_MBCR_ENG_NAME5, BYOV_MBCR_ENG_NAME1, BYOV_MBCR_ENG_NAME2, BYOV_MBCR_ENG_NAME3,
        BYOV_MBCR_ENG_NAME4, BYOV_MBCR_ENG_NAME5.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0NXMBC0", tr_key
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

    async def market_status_nxt_h0nxmko0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 장운영정보(NXT) -- WS tr_id=H0NXMKO0. 필수 파라미터:
        tr_key. 응답 필드: MKSC_SHRN_ISCD, TRHT_YN, TR_SUSP_REAS_CNTT, MKOP_CLS_CODE,
        ANTC_MKOP_CLS_CODE, MRKT_TRTM_CLS_CODE, DIVI_APP_CLS_CODE, ISCD_STAT_CLS_CODE,
        VI_CLS_CODE, OVTM_VI_CLS_CODE, EXCH_CLS_CODE.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0NXMKO0", tr_key
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

    async def program_trade_nxt_h0nxpgm0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 실시간프로그램매매 (NXT) -- WS tr_id=H0NXPGM0. 필수
        파라미터: tr_key. 응답 필드: MKSC_SHRN_ISCD, STCK_CNTG_HOUR, SELN_CNQN, SELN_TR_PBMN,
        SHNU_CNQN, SHNU_TR_PBMN, NTBY_CNQN, NTBY_TR_PBMN, SELN_RSQN, SHNU_RSQN, WHOL_NTBY_QTY.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0NXPGM0", tr_key
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
