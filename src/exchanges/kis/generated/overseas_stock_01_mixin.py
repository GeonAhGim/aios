"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_stock 미착수 TR 청크 01.

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


class KISGeneratedOverseasStock01Mixin(_KISRestHost, _KISWsHost):

    async def colable_by_company_ctln4050r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 당사 해외주식담보대출 가능 종목 [해외주식-051] -- GET
        /uapi/overseas-price/v1/quotations/colable-by-company (tr_id=CTLN4050R). 필수: PDNO,
        NATN_CD, INQR_SQN_DVSN. 선택: PRDT_TYPE_CD, INQR_STRT_DT, INQR_END_DT, INQR_DVSN,
        RT_DVSN_CD, RT, LOAN_PSBL_YN, CTX_AREA_FK100, CTX_AREA_NK100. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/colable-by-company",
            "CTLN4050R",
            params=params or {},
        )

    async def inquire_period_trans_ctos4001r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 일별거래내역 [해외주식-063] -- GET
        /uapi/overseas-stock/v1/trading/inquire-period-trans (tr_id=CTOS4001R). 필수: CANO,
        ACNT_PRDT_CD, ERLM_STRT_DT, ERLM_END_DT, OVRS_EXCG_CD, PDNO, SLL_BUY_DVSN_CD,
        LOAN_DVSN_CD, CTX_AREA_FK100, CTX_AREA_NK100. 선택: 없음. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-period-trans",
            "CTOS4001R",
            params=params or {},
        )

    async def countries_holiday_ctos5011r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외결제일자조회[해외주식-017] -- GET
        /uapi/overseas-stock/v1/quotations/countries-holiday (tr_id=CTOS5011R). 필수: TRAD_DT.
        선택: CTX_AREA_NK, CTX_AREA_FK. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/quotations/countries-holiday",
            "CTOS5011R",
            params=params or {},
        )

    async def search_info_ctpf1702r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 상품기본정보[v1_해외주식-034] -- GET
        /uapi/overseas-price/v1/quotations/search-info (tr_id=CTPF1702R). 필수: PRDT_TYPE_CD,
        PDNO. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/search-info",
            "CTPF1702R",
            params=params or {},
        )

    async def period_rights_ctrgt011r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 기간별권리조회 [해외주식-052] -- GET
        /uapi/overseas-price/v1/quotations/period-rights (tr_id=CTRGT011R). 필수: RGHT_TYPE_CD,
        INQR_DVSN_CD, INQR_STRT_DT, INQR_END_DT. 선택: PDNO, PRDT_TYPE_CD, CTX_AREA_NK50,
        CTX_AREA_FK50. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/period-rights",
            "CTRGT011R",
            params=params or {},
        )

    async def inquire_paymt_stdr_balance_ctrp6010r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 결제기준잔고 [해외주식-064] -- GET
        /uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance (tr_id=CTRP6010R). 필수:
        CANO, ACNT_PRDT_CD, BASS_DT, WCRC_FRCR_DVSN_CD, INQR_DVSN_CD. 선택: 없음. 응답 컨테이너:
        output1, output2, output3.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance",
            "CTRP6010R",
            params=params or {},
        )

    async def inquire_present_balance_ctrp6504r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 체결기준현재잔고 [v1_해외주식-008] -- GET
        /uapi/overseas-stock/v1/trading/inquire-present-balance (tr_id=CTRP6504R). 필수: CANO,
        ACNT_PRDT_CD, WCRC_FRCR_DVSN_CD, NATN_CD, TR_MKET_CD, INQR_DVSN_CD. 선택: 없음. 응답
        컨테이너: output1, output2, output3.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            "CTRP6504R",
            params=params or {},
        )

    async def brknews_title_fhkst01011801(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외속보(제목) [해외주식-055] -- GET
        /uapi/overseas-price/v1/quotations/brknews-title (tr_id=FHKST01011801). 필수:
        FID_NEWS_OFER_ENTP_CODE, FID_COND_SCR_DIV_CODE. 선택: FID_COND_MRKT_CLS_CODE,
        FID_INPUT_ISCD, FID_TITL_CNTT, FID_INPUT_DATE_1, FID_INPUT_HOUR_1,
        FID_RANK_SORT_CLS_CODE, FID_INPUT_SRNO. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/brknews-title",
            "FHKST01011801",
            params=params or {},
        )

    async def inquire_daily_chartprice_fhkst03030100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 종목_지수_환율기간별시세(일_주_월_년)[v1_해외주식-012] --
        GET /uapi/overseas-price/v1/quotations/inquire-daily-chartprice (tr_id=FHKST03030100).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1, FID_INPUT_DATE_2,
        FID_PERIOD_DIV_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice",
            "FHKST03030100",
            params=params or {},
        )

    async def inquire_time_indexchartprice_fhkst03030200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외지수분봉조회[v1_해외주식-031] -- GET
        /uapi/overseas-price/v1/quotations/inquire-time-indexchartprice (tr_id=FHKST03030200).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_HOUR_CLS_CODE, FID_PW_DATA_INCU_YN.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice",
            "FHKST03030200",
            params=params or {},
        )

    async def ccnl_notice_h0gscni0(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 실시간시세 > 해외주식 실시간체결통보[실시간-009] -- WS tr_id=H0GSCNI0. 필수
        파라미터: tr_key. 응답 필드: CUST_ID, ACNT_NO, ODER_NO, OODER_NO, SELN_BYOV_CLS,
        RCTF_CLS, ODER_KIND2, STCK_SHRN_ISCD, CNTG_QTY, CNTG_UNPR, STCK_CNTG_HOUR, RFUS_YN,
        CNTG_YN, ACPT_YN, BRNC_NO, ODER_QTY, ACNT_NAME, CNTG_ISNM, ODER_COND, DEBT_GB,
        DEBT_DATE, START_TM, END_TM, TM_DIV_TP, CNTG_UNPR12.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0GSCNI0", tr_key
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

    async def ccnl_notice_h0gscni9(
        self,
        tr_key: str,
        callback: MessageHandler,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 실시간시세 > 해외주식 실시간체결통보[실시간-009] -- WS tr_id=H0GSCNI9. 필수
        파라미터: tr_key. 응답 필드: CUST_ID, ACNT_NO, ODER_NO, OODER_NO, SELN_BYOV_CLS,
        RCTF_CLS, ODER_KIND2, STCK_SHRN_ISCD, CNTG_QTY, CNTG_UNPR, STCK_CNTG_HOUR, RFUS_YN,
        CNTG_YN, ACPT_YN, BRNC_NO, ODER_QTY, ACNT_NAME, CNTG_ISNM, ODER_COND, DEBT_GB,
        DEBT_DATE, START_TM, END_TM, TM_DIV_TP, CNTG_UNPR12.
        """
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL
        subscribe_msg = _build_subscribe_message(
            approval_key, "H0GSCNI9", tr_key
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
