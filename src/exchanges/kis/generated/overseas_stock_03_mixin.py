"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_stock 미착수 TR 청크 03.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedOverseasStock03Mixin(_KISRestHost):

    async def industry_price_hhdfs76370100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식 업종별코드조회[해외주식-049] -- GET
        /uapi/overseas-price/v1/quotations/industry-price (tr_id=HHDFS76370100). 필수: EXCD.
        선택: AUTH. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/industry-price",
            "HHDFS76370100",
            params=params or {},
        )

    async def inquire_search_hhdfs76410000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식조건검색[v1_해외주식-015] -- GET
        /uapi/overseas-price/v1/quotations/inquire-search (tr_id=HHDFS76410000). 필수: AUTH,
        EXCD, CO_YN_PRICECUR, CO_ST_PRICECUR, CO_EN_PRICECUR, CO_YN_RATE, CO_ST_RATE,
        CO_EN_RATE, CO_YN_VALX, CO_ST_VALX, CO_EN_VALX, CO_YN_SHAR, CO_ST_SHAR, CO_EN_SHAR,
        CO_YN_VOLUME, CO_ST_VOLUME, CO_EN_VOLUME, CO_YN_AMT, CO_ST_AMT, CO_EN_AMT, CO_YN_EPS,
        CO_ST_EPS, CO_EN_EPS, CO_YN_PER, CO_ST_PER, CO_EN_PER, KEYB. 선택: 없음. 응답 컨테이너:
        output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/inquire-search",
            "HHDFS76410000",
            params=params or {},
        )

    async def inquire_time_itemchartprice_hhdfs76950200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 기본시세 > 해외주식분봉조회[v1_해외주식-030] -- GET
        /uapi/overseas-price/v1/quotations/inquire-time-itemchartprice (tr_id=HHDFS76950200).
        필수: AUTH, EXCD, SYMB, NMIN, PINC, NEXT, NREC, FILL, KEYB. 선택: 없음. 응답 컨테이너:
        output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
            "HHDFS76950200",
            params=params or {},
        )

    async def rights_by_ice_hhdfs78330900(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외주식 권리종합 [해외주식-050] -- GET
        /uapi/overseas-price/v1/quotations/rights-by-ice (tr_id=HHDFS78330900). 필수: NCOD,
        SYMB. 선택: ST_YMD, ED_YMD. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/rights-by-ice",
            "HHDFS78330900",
            params=params or {},
        )

    async def news_title_hhpsth60100c1(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 시세분석 > 해외뉴스종합(제목) [해외주식-053] -- GET
        /uapi/overseas-price/v1/quotations/news-title (tr_id=HHPSTH60100C1). 필수: 없음. 선택:
        INFO_GB, CLASS_CD, NATION_CD, EXCHANGE_CD, SYMB, DATA_DT, DATA_TM, CTS. 응답 컨테이너:
        outblock1.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/news-title",
            "HHPSTH60100C1",
            params=params or {},
        )

    async def inquire_psamount_ttts3007r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 매수가능금액조회 [v1_해외주식-014] -- GET
        /uapi/overseas-stock/v1/trading/inquire-psamount (tr_id=TTTS3007R). 필수: CANO,
        ACNT_PRDT_CD, OVRS_EXCG_CD, OVRS_ORD_UNPR, ITEM_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            "TTTS3007R",
            params=params or {},
        )

    async def order_resv_ttts3013u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] -- POST
        /uapi/overseas-stock/v1/trading/order-resv (tr_id=TTTS3013U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, OVRS_EXCG_CD, FT_ORD_QTY, FT_ORD_UNPR3. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv",
            "TTTS3013U",
            body=params or {},
        )

    async def inquire_ccnl_ttts3035r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 주문체결내역 [v1_해외주식-007] -- GET
        /uapi/overseas-stock/v1/trading/inquire-ccnl (tr_id=TTTS3035R). 필수: CANO,
        ACNT_PRDT_CD, PDNO, ORD_STRT_DT, ORD_END_DT, SLL_BUY_DVSN, CCLD_NCCS_DVSN, SORT_SQN,
        ORD_DT, ORD_GNO_BRNO, ODNO. 선택: OVRS_EXCG_CD, CTX_AREA_NK200, CTX_AREA_FK200. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-ccnl",
            "TTTS3035R",
            params=params or {},
        )

    async def order_resv_tttt3014u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] -- POST
        /uapi/overseas-stock/v1/trading/order-resv (tr_id=TTTT3014U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, OVRS_EXCG_CD, FT_ORD_QTY, FT_ORD_UNPR3. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv",
            "TTTT3014U",
            body=params or {},
        )

    async def order_resv_tttt3016u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] -- POST
        /uapi/overseas-stock/v1/trading/order-resv (tr_id=TTTT3016U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, OVRS_EXCG_CD, FT_ORD_QTY, FT_ORD_UNPR3. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv",
            "TTTT3016U",
            body=params or {},
        )

    async def order_resv_ccnl_tttt3017u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수취소[v1_해외주식-004] -- POST
        /uapi/overseas-stock/v1/trading/order-resv-ccnl (tr_id=TTTT3017U). 필수: CANO,
        ACNT_PRDT_CD, RSVN_ORD_RCIT_DT, OVRS_RSVN_ODNO. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv-ccnl",
            "TTTT3017U",
            body=params or {},
        )

    async def inquire_present_balance_vtrp6504r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 체결기준현재잔고 [v1_해외주식-008] -- GET
        /uapi/overseas-stock/v1/trading/inquire-present-balance (tr_id=VTRP6504R). 필수: CANO,
        ACNT_PRDT_CD, WCRC_FRCR_DVSN_CD, NATN_CD, TR_MKET_CD, INQR_DVSN_CD. 선택: 없음. 응답
        컨테이너: output1, output2, output3.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            "VTRP6504R",
            params=params or {},
        )

    async def inquire_psamount_vtts3007r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 매수가능금액조회 [v1_해외주식-014] -- GET
        /uapi/overseas-stock/v1/trading/inquire-psamount (tr_id=VTTS3007R). 필수: CANO,
        ACNT_PRDT_CD, OVRS_EXCG_CD, OVRS_ORD_UNPR, ITEM_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            "VTTS3007R",
            params=params or {},
        )

    async def inquire_balance_vtts3012r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 잔고 [v1_해외주식-006] -- GET
        /uapi/overseas-stock/v1/trading/inquire-balance (tr_id=VTTS3012R). 필수: CANO,
        ACNT_PRDT_CD, OVRS_EXCG_CD, TR_CRCY_CD. 선택: CTX_AREA_FK200, CTX_AREA_NK200. 응답
        컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            "VTTS3012R",
            params=params or {},
        )

    async def order_resv_vtts3013u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] -- POST
        /uapi/overseas-stock/v1/trading/order-resv (tr_id=VTTS3013U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, OVRS_EXCG_CD, FT_ORD_QTY, FT_ORD_UNPR3. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv",
            "VTTS3013U",
            body=params or {},
        )

    async def inquire_ccnl_vtts3035r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 주문체결내역 [v1_해외주식-007] -- GET
        /uapi/overseas-stock/v1/trading/inquire-ccnl (tr_id=VTTS3035R). 필수: CANO,
        ACNT_PRDT_CD, PDNO, ORD_STRT_DT, ORD_END_DT, SLL_BUY_DVSN, CCLD_NCCS_DVSN, SORT_SQN,
        ORD_DT, ORD_GNO_BRNO, ODNO. 선택: OVRS_EXCG_CD, CTX_AREA_NK200, CTX_AREA_FK200. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-ccnl",
            "VTTS3035R",
            params=params or {},
        )
