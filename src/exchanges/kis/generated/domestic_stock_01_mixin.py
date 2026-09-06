"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 01.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock01Mixin(_KISRestHost):

    async def search_stock_info_ctpf1002r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 주식기본조회 -- GET
        /uapi/domestic-stock/v1/quotations/search-stock-info (tr_id=CTPF1002R). 필수:
        PRDT_TYPE_CD, PDNO. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/search-stock-info",
            "CTPF1002R",
            params=params or {},
        )

    async def search_info_ctpf1604r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 상품기본조회 -- GET /uapi/domestic-stock/v1/quotations/search-info
        (tr_id=CTPF1604R). 필수: PDNO, PRDT_TYPE_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/search-info",
            "CTPF1604R",
            params=params or {},
        )

    async def period_rights_ctrga011r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 기간별계좌권리현황조회 [국내주식-211] -- GET
        /uapi/domestic-stock/v1/trading/period-rights (tr_id=CTRGA011R). 필수: INQR_DVSN, CANO,
        ACNT_PRDT_CD, INQR_STRT_DT, INQR_END_DT. 선택: CUST_RNCNO25, HMID, RGHT_TYPE_CD, PDNO,
        PRDT_TYPE_CD, CTX_AREA_NK100, CTX_AREA_FK100. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/period-rights",
            "CTRGA011R",
            params=params or {},
        )

    async def inquire_account_balance_ctrp6548r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 투자계좌자산현황조회[v1_국내주식-048] -- GET
        /uapi/domestic-stock/v1/trading/inquire-account-balance (tr_id=CTRP6548R). 필수: CANO,
        ACNT_PRDT_CD. 선택: INQR_DVSN_1, BSPR_BF_DT_APLY_YN. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-account-balance",
            "CTRP6548R",
            params=params or {},
        )

    async def order_resv_ccnl_ctsc0004r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식예약주문조회[v1_국내주식-020] -- GET
        /uapi/domestic-stock/v1/trading/order-resv-ccnl (tr_id=CTSC0004R). 필수:
        RSVN_ORD_ORD_DT, RSVN_ORD_END_DT, TMNL_MDIA_KIND_CD, CANO, ACNT_PRDT_CD, PRCS_DVSN_CD,
        CNCL_YN. 선택: RSVN_ORD_SEQ, PDNO, SLL_BUY_DVSN_CD, CTX_AREA_FK200, CTX_AREA_NK200. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/order-resv-ccnl",
            "CTSC0004R",
            params=params or {},
        )

    async def order_resv_ctsc0008u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식예약주문[v1_국내주식-017] -- POST
        /uapi/domestic-stock/v1/trading/order-resv (tr_id=CTSC0008U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, ORD_QTY, ORD_UNPR, SLL_BUY_DVSN_CD, ORD_DVSN_CD, ORD_OBJT_CBLC_DVSN_CD. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-resv",
            "CTSC0008U",
            body=params or {},
        )

    async def order_resv_rvsecncl_ctsc0009u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식예약주문정정취소[v1_국내주식-018,019] -- POST
        /uapi/domestic-stock/v1/trading/order-resv-rvsecncl (tr_id=CTSC0009U). 필수: CANO,
        ACNT_PRDT_CD, RSVN_ORD_SEQ, RSVN_ORD_ORGNO, RSVN_ORD_ORD_DT. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-resv-rvsecncl",
            "CTSC0009U",
            body=params or {},
        )

    async def order_resv_rvsecncl_ctsc0013u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식예약주문정정취소[v1_국내주식-018,019] -- POST
        /uapi/domestic-stock/v1/trading/order-resv-rvsecncl (tr_id=CTSC0013U). 필수: CANO,
        ACNT_PRDT_CD, RSVN_ORD_SEQ, RSVN_ORD_ORGNO, RSVN_ORD_ORD_DT. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-resv-rvsecncl",
            "CTSC0013U",
            body=params or {},
        )

    async def inquire_daily_ccld_ctsc9215r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] -- GET
        /uapi/domestic-stock/v1/trading/inquire-daily-ccld (tr_id=CTSC9215R). 필수: CANO,
        ACNT_PRDT_CD, INQR_STRT_DT, INQR_END_DT, SLL_BUY_DVSN_CD, CCLD_DVSN, INQR_DVSN,
        INQR_DVSN_3. 선택: PDNO, ORD_GNO_BRNO, ODNO, INQR_DVSN_1, CTX_AREA_FK100,
        CTX_AREA_NK100. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "CTSC9215R",
            params=params or {},
        )

    async def inquire_ccnl_fhkst01010300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 체결[v1_국내주식-009] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-ccnl (tr_id=FHKST01010300). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-ccnl",
            "FHKST01010300",
            params=params or {},
        )

    async def inquire_daily_price_fhkst01010400(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 일자별[v1_국내주식-010] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-daily-price (tr_id=FHKST01010400). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_PERIOD_DIV_CODE, FID_ORG_ADJ_PRC. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            "FHKST01010400",
            params=params or {},
        )

    async def inquire_member_fhkst01010600(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 회원사[v1_국내주식-013] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-member (tr_id=FHKST01010600). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-member",
            "FHKST01010600",
            params=params or {},
        )

    async def news_title_fhkst01011800(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 - 종합 시황/공시(제목) -- GET
        /uapi/domestic-stock/v1/quotations/news-title (tr_id=FHKST01011800). 필수:
        FID_NEWS_OFER_ENTP_CODE, FID_COND_MRKT_CLS_CODE, FID_INPUT_ISCD, FID_TITL_CNTT,
        FID_INPUT_DATE_1, FID_INPUT_HOUR_1, FID_RANK_SORT_CLS_CODE, FID_INPUT_SRNO. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/news-title",
            "FHKST01011800",
            params=params or {},
        )

    async def inquire_time_dailychartprice_fhkst03010230(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식일별분봉조회 [국내주식-213] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice (tr_id=FHKST03010230).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_HOUR_1, FID_INPUT_DATE_1. 선택:
        FID_PW_DATA_INCU_YN, FID_FAKE_TICK_INCU_YN. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
            "FHKST03010230",
            params=params or {},
        )

    async def inquire_daily_trade_volume_fhkst03010800(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목별일별매수매도체결량 [v1_국내주식-056] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-daily-trade-volume (tr_id=FHKST03010800).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_PERIOD_DIV_CODE. 선택:
        FID_INPUT_DATE_1, FID_INPUT_DATE_2. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-trade-volume",
            "FHKST03010800",
            params=params or {},
        )

    async def tradprt_byamt_fhkst111900c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내주식 체결금액별 매매비중 [국내주식-192] -- GET
        /uapi/domestic-stock/v1/quotations/tradprt-byamt (tr_id=FHKST111900C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/tradprt-byamt",
            "FHKST111900C0",
            params=params or {},
        )
