"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 09.

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


class KISGeneratedDomesticStock09Mixin(_KISRestHost, _KISWsHost):

    async def program_trade_total_h0unpgm0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내주식 실시간프로그램매매 (통합) -- WS tr_id=H0UNPGM0. 필수
        파라미터: tr_key. 응답 필드: MKSC_SHRN_ISCD, STCK_CNTG_HOUR, SELN_CNQN, SELN_TR_PBMN,
        SHNU_CNQN, SHNU_TR_PBMN, NTBY_CNQN, NTBY_TR_PBMN, SELN_RSQN, SHNU_RSQN, WHOL_NTBY_QTY.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0UNPGM0", tr_key
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

    async def index_exp_ccnl_h0upanc0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내지수 실시간예상체결 [실시간-027] -- WS tr_id=H0UPANC0. 필수
        파라미터: tr_key. 응답 필드: bstp_cls_code, bsop_hour, prpr_nmix, prdy_vrss_sign,
        bstp_nmix_prdy_vrss, acml_vol, acml_tr_pbmn, pcas_vol, pcas_tr_pbmn, prdy_ctrt,
        oprc_nmix, nmix_hgpr, nmix_lwpr, oprc_vrss_nmix_prpr, oprc_vrss_nmix_sign,
        hgpr_vrss_nmix_prpr, hgpr_vrss_nmix_sign, lwpr_vrss_nmix_prpr, lwpr_vrss_nmix_sign,
        prdy_clpr_vrss_oprc_rate, prdy_clpr_vrss_hgpr_rate, prdy_clpr_vrss_lwpr_rate,
        uplm_issu_cnt, ascn_issu_cnt, stnr_issu_cnt, down_issu_cnt, lslm_issu_cnt,
        qtqt_ascn_issu_cnt, qtqt_down_issu_cnt, tick_vrss.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0UPANC0", tr_key
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

    async def index_ccnl_h0upcnt0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내지수 실시간체결 [실시간-026] -- WS tr_id=H0UPCNT0. 필수
        파라미터: tr_key. 응답 필드: bstp_cls_code, bsop_hour, prpr_nmix, prdy_vrss_sign,
        bstp_nmix_prdy_vrss, acml_vol, acml_tr_pbmn, pcas_vol, pcas_tr_pbmn, prdy_ctrt,
        oprc_nmix, nmix_hgpr, nmix_lwpr, oprc_vrss_nmix_prpr, oprc_vrss_nmix_sign,
        hgpr_vrss_nmix_prpr, hgpr_vrss_nmix_sign, lwpr_vrss_nmix_prpr, lwpr_vrss_nmix_sign,
        prdy_clpr_vrss_oprc_rate, prdy_clpr_vrss_hgpr_rate, prdy_clpr_vrss_lwpr_rate,
        uplm_issu_cnt, ascn_issu_cnt, stnr_issu_cnt, down_issu_cnt, lslm_issu_cnt,
        qtqt_ascn_issu_cnt, qtqt_down_issu_cnt, tick_vrss.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0UPCNT0", tr_key
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

    async def index_program_trade_h0uppgm0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 실시간시세 > 국내지수 실시간프로그램매매 [실시간-028] -- WS tr_id=H0UPPGM0.
        필수 파라미터: tr_key. 응답 필드: bstp_cls_code, bsop_hour, arbt_seln_entm_cnqn,
        arbt_seln_onsl_cnqn, arbt_shnu_entm_cnqn, arbt_shnu_onsl_cnqn, nabt_seln_entm_cnqn,
        nabt_seln_onsl_cnqn, nabt_shnu_entm_cnqn, nabt_shnu_onsl_cnqn, arbt_seln_entm_cntg_amt,
        arbt_seln_onsl_cntg_amt, arbt_shnu_entm_cntg_amt, arbt_shnu_onsl_cntg_amt,
        nabt_seln_entm_cntg_amt, nabt_seln_onsl_cntg_amt, nabt_shnu_entm_cntg_amt,
        nabt_shnu_onsl_cntg_amt, arbt_smtn_seln_vol, arbt_smtm_seln_vol_rate,
        arbt_smtn_seln_tr_pbmn, arbt_smtm_seln_tr_pbmn_rate, arbt_smtn_shnu_vol,
        arbt_smtm_shnu_vol_rate, arbt_smtn_shnu_tr_pbmn, arbt_smtm_shnu_tr_pbmn_rate,
        arbt_smtn_ntby_qty, arbt_smtm_ntby_qty_rate, arbt_smtn_ntby_tr_pbmn,
        arbt_smtm_ntby_tr_pbmn_rate, nabt_smtn_seln_vol, nabt_smtm_seln_vol_rate,
        nabt_smtn_seln_tr_pbmn, nabt_smtm_seln_tr_pbmn_rate, nabt_smtn_shnu_vol,
        nabt_smtm_shnu_vol_rate, nabt_smtn_shnu_tr_pbmn, nabt_smtm_shnu_tr_pbmn_rate,
        nabt_smtn_ntby_qty, nabt_smtm_ntby_qty_rate, nabt_smtn_ntby_tr_pbmn,
        nabt_smtm_ntby_tr_pbmn_rate, whol_entm_seln_vol, entm_seln_vol_rate,
        whol_entm_seln_tr_pbmn, entm_seln_tr_pbmn_rate, whol_entm_shnu_vol, entm_shnu_vol_rate,
        whol_entm_shnu_tr_pbmn, entm_shnu_tr_pbmn_rate, whol_entm_ntby_qt, entm_ntby_qty_rat,
        whol_entm_ntby_tr_pbmn, entm_ntby_tr_pbmn_rate, whol_onsl_seln_vol, onsl_seln_vol_rate,
        whol_onsl_seln_tr_pbmn, onsl_seln_tr_pbmn_rate, whol_onsl_shnu_vol, onsl_shnu_vol_rate,
        whol_onsl_shnu_tr_pbmn, onsl_shnu_tr_pbmn_rate, whol_onsl_ntby_qty, onsl_ntby_qty_rate,
        whol_onsl_ntby_tr_pbmn, onsl_ntby_tr_pbmn_rate, total_seln_qty, whol_seln_vol_rate,
        total_seln_tr_pbmn, whol_seln_tr_pbmn_rate, shnu_cntg_smtn, whol_shun_vol_rate,
        total_shnu_tr_pbmn, whol_shun_tr_pbmn_rate, whol_ntby_qty, whol_smtm_ntby_qty_rate,
        whol_ntby_tr_pbmn, whol_ntby_tr_pbmn_rate, arbt_entm_ntby_qty, arbt_entm_ntby_tr_pbmn,
        arbt_onsl_ntby_qty, arbt_onsl_ntby_tr_pbmn, nabt_entm_ntby_qty, nabt_entm_ntby_tr_pbmn,
        nabt_onsl_ntby_qty, nabt_onsl_ntby_tr_pbmn, acml_vol, acml_tr_pbmn.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0UPPGM0", tr_key
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

    async def intstock_stocklist_by_group_hhkcm113004c6(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 관심종목 그룹별 종목조회 [국내주식-203] -- GET
        /uapi/domestic-stock/v1/quotations/intstock-stocklist-by-group (tr_id=HHKCM113004C6).
        필수: TYPE, USER_ID, INTER_GRP_CODE, FID_ETC_CLS_CODE. 선택: DATA_RANK, INTER_GRP_NAME,
        HTS_KOR_ISNM, CNTG_CLS_CODE. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/intstock-stocklist-by-group",
            "HHKCM113004C6",
            params=params or {},
        )

    async def intstock_grouplist_hhkcm113004c7(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 관심종목 그룹조회 [국내주식-204] -- GET
        /uapi/domestic-stock/v1/quotations/intstock-grouplist (tr_id=HHKCM113004C7). 필수: TYPE,
        FID_ETC_CLS_CODE, USER_ID. 선택: 없음. 응답 컨테이너: output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/intstock-grouplist",
            "HHKCM113004C7",
            params=params or {},
        )

    async def dividend_rate_hhkdb13470100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 배당률 상위[국내주식-106] -- GET
        /uapi/domestic-stock/v1/ranking/dividend-rate (tr_id=HHKDB13470100). 필수: CTS_AREA,
        GB1, UPJONG, GB2, GB3, F_DT, T_DT, GB4. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/dividend-rate",
            "HHKDB13470100",
            params=params or {},
        )

    async def ksdinfo_paidin_capin_hhkdb669100c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 예탁원정보(유상증자일정)[국내주식-143] -- GET
        /uapi/domestic-stock/v1/ksdinfo/paidin-capin (tr_id=HHKDB669100C0). 필수: CTS, GB1,
        F_DT, T_DT, SHT_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/paidin-capin",
            "HHKDB669100C0",
            params=params or {},
        )

    async def ksdinfo_bonus_issue_hhkdb669101c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 예탁원정보(무상증자일정) -- GET
        /uapi/domestic-stock/v1/ksdinfo/bonus-issue (tr_id=HHKDB669101C0). 필수: CTS, F_DT,
        T_DT, SHT_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/bonus-issue",
            "HHKDB669101C0",
            params=params or {},
        )

    async def ksdinfo_purreq_hhkdb669103c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 예탁원정보(주식매수청구일정) -- GET
        /uapi/domestic-stock/v1/ksdinfo/purreq (tr_id=HHKDB669103C0). 필수: SHT_CD, T_DT, F_DT,
        CTS. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/purreq",
            "HHKDB669103C0",
            params=params or {},
        )
