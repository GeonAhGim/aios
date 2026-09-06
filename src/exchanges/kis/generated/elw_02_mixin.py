"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- elw 미착수 TR 청크 02.

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


class KISGeneratedElw02Mixin(_KISRestHost, _KISWsHost):

    async def volatility_trend_daily_fhpew02840200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 변동성추이(일별)[국내주식-178] -- GET
        /uapi/elw/v1/quotations/volatility-trend-daily (tr_id=FHPEW02840200). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/volatility-trend-daily",
            "FHPEW02840200",
            params=params or {},
        )

    async def volatility_trend_minute_fhpew02840300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 변동성 추이(분별) -- GET
        /uapi/elw/v1/quotations/volatility-trend-minute (tr_id=FHPEW02840300). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_HOUR_CLS_CODE, FID_PW_DATA_INCU_YN. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/volatility-trend-minute",
            "FHPEW02840300",
            params=params or {},
        )

    async def volatility_trend_tick_fhpew02840400(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 변동성추이(틱)[국내주식-180] -- GET
        /uapi/elw/v1/quotations/volatility-trend-tick (tr_id=FHPEW02840400). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/volatility-trend-tick",
            "FHPEW02840400",
            params=params or {},
        )

    async def sensitivity_fhpew02850000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 민감도 순위[국내주식-170] -- GET
        /uapi/elw/v1/ranking/sensitivity (tr_id=FHPEW02850000). 필수: FID_COND_MRKT_DIV_CODE,
        FID_COND_SCR_DIV_CODE, FID_UNAS_INPUT_ISCD, FID_INPUT_ISCD, FID_DIV_CLS_CODE,
        FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_INPUT_VOL_1, FID_INPUT_VOL_2,
        FID_RANK_SORT_CLS_CODE, FID_INPUT_RMNN_DYNU_1, FID_INPUT_DATE_1, FID_BLNG_CLS_CODE.
        선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/ranking/sensitivity",
            "FHPEW02850000",
            params=params or {},
        )

    async def quick_change_fhpew02870000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 당일급변종목[국내주식-171] -- GET
        /uapi/elw/v1/ranking/quick-change (tr_id=FHPEW02870000). 필수: FID_COND_MRKT_DIV_CODE,
        FID_COND_SCR_DIV_CODE, FID_UNAS_INPUT_ISCD, FID_INPUT_ISCD, FID_MRKT_CLS_CODE,
        FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_INPUT_VOL_1, FID_INPUT_VOL_2,
        FID_HOUR_CLS_CODE, FID_INPUT_HOUR_1, FID_INPUT_HOUR_2, FID_RANK_SORT_CLS_CODE,
        FID_BLNG_CLS_CODE. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/ranking/quick-change",
            "FHPEW02870000",
            params=params or {},
        )

    async def lp_trade_trend_fhpew03760000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW LP매매추이[국내주식-182] -- GET
        /uapi/elw/v1/quotations/lp-trade-trend (tr_id=FHPEW03760000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/lp-trade-trend",
            "FHPEW03760000",
            params=params or {},
        )

    async def elw_exp_ccnl_h0ewanc0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 - ELW 실시간예상체결[실시간-063] -- WS tr_id=H0EWANC0. 필수
        파라미터: tr_key. 응답 필드: mksc_shrn_iscd, stck_cntg_hour, stck_prpr, prdy_vrss_sign,
        prdy_vrss, prdy_ctrt, wghn_avrg_stck_prc, stck_oprc, stck_hgpr, stck_lwpr, askp1, bidp1,
        cntg_vol, acml_vol, acml_tr_pbmn, seln_cntg_csnu, shnu_cntg_csnu, ntby_cntg_csnu, cttr,
        seln_cntg_smtn, shnu_cntg_smtn, cntg_cls_code, shnu_rate, prdy_vol_vrss_acml_vol_rate,
        oprc_hour, oprc_vrss_prpr_sign, oprc_vrss_prpr, hgpr_hour, hgpr_vrss_prpr_sign,
        hgpr_vrss_prpr, lwpr_hour, lwpr_vrss_prpr_sign, lwpr_vrss_prpr, bsop_date,
        new_mkop_cls_code, trht_yn, askp_rsqn1, bidp_rsqn1, total_askp_rsqn, total_bidp_rsqn,
        tmvl_val, prit, prmm_val, gear, prls_qryr_rate, invl_val, prmm_rate, cfp, lvrg_val,
        delta, gama, vega, theta, rho, hts_ints_vltl, hts_thpr, vol_tnrt, lp_hvol, lp_hldn_rate.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0EWANC0", tr_key
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

    async def elw_asking_price_h0ewasp0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 - ELW 실시간호가[실시간-062] -- WS tr_id=H0EWASP0. 필수 파라미터:
        tr_key. 응답 필드: mksc_shrn_iscd, bsop_hour, hour_cls_code, askp1, askp2, askp3, askp4,
        askp5, askp6, askp7, askp8, askp9, askp10, bidp1, bidp2, bidp3, bidp4, bidp5, bidp6,
        bidp7, bidp8, bidp9, bidp10, askp_rsqn1, askp_rsqn2, askp_rsqn3, askp_rsqn4, askp_rsqn5,
        askp_rsqn6, askp_rsqn7, askp_rsqn8, askp_rsqn9, askp_rsqn10, bidp_rsqn1, bidp_rsqn2,
        bidp_rsqn3, bidp_rsqn4, bidp_rsqn5, bidp_rsqn6, bidp_rsqn7, bidp_rsqn8, bidp_rsqn9,
        bidp_rsqn10, total_askp_rsqn, total_bidp_rsqn, antc_cnpr, antc_cnqn,
        antc_cntg_vrss_sign, antc_cntg_vrss, antc_cntg_prdy_ctrt, lp_askp_rsqn1, lp_askp_rsqn2,
        lp_askp_rsqn3, lp_bidp_rsqn4, lp_askp_rsqn4, lp_bidp_rsqn5, lp_askp_rsqn5,
        lp_bidp_rsqn6, lp_askp_rsqn6, lp_bidp_rsqn7, lp_askp_rsqn7, lp_askp_rsqn8,
        lp_bidp_rsqn8, lp_askp_rsqn9, lp_bidp_rsqn9, lp_askp_rsqn10, lp_bidp_rsqn10,
        lp_bidp_rsqn1, lp_total_askp_rsqn, lp_bidp_rsqn2, lp_total_bidp_rsqn, lp_bidp_rsqn3,
        antc_vol.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0EWASP0", tr_key
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

    async def elw_ccnl_h0ewcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 - ELW 실시간체결가[실시간-061] -- WS tr_id=H0EWCNT0. 필수
        파라미터: tr_key. 응답 필드: mksc_shrn_iscd, stck_cntg_hour, stck_prpr, prdy_vrss_sign,
        prdy_vrss, prdy_ctrt, wghn_avrg_stck_prc, stck_oprc, stck_hgpr, stck_lwpr, askp1, bidp1,
        cntg_vol, acml_vol, acml_tr_pbmn, seln_cntg_csnu, shnu_cntg_csnu, ntby_cntg_csnu, cttr,
        seln_cntg_smtn, shnu_cntg_smtn, cntg_cls_code, shnu_rate, prdy_vol_vrss_acml_vol_rate,
        oprc_hour, oprc_vrss_prpr_sign, oprc_vrss_prpr, hgpr_hour, hgpr_vrss_prpr_sign,
        hgpr_vrss_prpr, lwpr_hour, lwpr_vrss_prpr_sign, lwpr_vrss_prpr, bsop_date,
        new_mkop_cls_code, trht_yn, askp_rsqn1, bidp_rsqn1, total_askp_rsqn, total_bidp_rsqn,
        tmvl_val, prit, prmm_val, gear, prls_qryr_rate, invl_val, prmm_rate, cfp, lvrg_val,
        delta, gama, vega, theta, rho, hts_ints_vltl, hts_thpr, vol_tnrt,
        prdy_smns_hour_acml_vol, prdy_smns_hour_acml_vol_rate, apprch_rate, lp_hvol,
        lp_hldn_rate, lp_ntby_qty.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0EWCNT0", tr_key
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
