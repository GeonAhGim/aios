"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_futureoption 미착수 TR 청크 02.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.common.live_guard import require_paper_sandbox
from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedOverseasFutureoption02Mixin(_KISRestHost):

    async def search_contract_detail_hhdfc55200000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 상품기본정보[해외선물-023] -- GET
        /uapi/overseas-futureoption/v1/quotations/search-contract-detail (tr_id=HHDFC55200000).
        필수: QRY_CNT. 선택: 없음. 응답 컨테이너: output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/search-contract-detail",
            "HHDFC55200000",
            params=params or {},
        )

    async def inquire_asking_price_hhdfc86000000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물 호가 [해외선물-031] -- GET
        /uapi/overseas-futureoption/v1/quotations/inquire-asking-price (tr_id=HHDFC86000000).
        필수: SRS_CD. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/inquire-asking-price",
            "HHDFC86000000",
            params=params or {},
        )

    async def opt_detail_hhdfo55010100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션종목상세 [해외선물-034] -- GET
        /uapi/overseas-futureoption/v1/quotations/opt-detail (tr_id=HHDFO55010100). 필수:
        SRS_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/opt-detail",
            "HHDFO55010100",
            params=params or {},
        )

    async def opt_weekly_ccnl_hhdfo55020000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션 체결추이(주간) [해외선물-036] -- GET
        /uapi/overseas-futureoption/v1/quotations/opt-weekly-ccnl (tr_id=HHDFO55020000). 필수:
        SRS_CD, EXCH_CD, QRY_CNT. 선택: START_DATE_TIME, CLOSE_DATE_TIME, QRY_GAP, QRY_TP,
        INDEX_KEY. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/opt-weekly-ccnl",
            "HHDFO55020000",
            params=params or {},
        )

    async def inquire_time_optchartprice_hhdfo55020100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션 분봉조회 [해외선물-040] -- GET
        /uapi/overseas-futureoption/v1/quotations/inquire-time-optchartprice
        (tr_id=HHDFO55020100). 필수: SRS_CD, EXCH_CD, QRY_CNT. 선택: START_DATE_TIME,
        CLOSE_DATE_TIME, QRY_GAP, QRY_TP, INDEX_KEY. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/inquire-time-optchartprice",
            "HHDFO55020100",
            params=params or {},
        )

    async def opt_tick_ccnl_hhdfo55020200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션 체결추이(틱) [해외선물-038] -- GET
        /uapi/overseas-futureoption/v1/quotations/opt-tick-ccnl (tr_id=HHDFO55020200). 필수:
        SRS_CD, EXCH_CD, QRY_CNT. 선택: START_DATE_TIME, CLOSE_DATE_TIME, QRY_GAP, QRY_TP,
        INDEX_KEY. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/opt-tick-ccnl",
            "HHDFO55020200",
            params=params or {},
        )

    async def opt_monthly_ccnl_hhdfo55020300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션 체결추이(월간) [해외선물-039] -- GET
        /uapi/overseas-futureoption/v1/quotations/opt-monthly-ccnl (tr_id=HHDFO55020300). 필수:
        SRS_CD, EXCH_CD, QRY_CNT. 선택: START_DATE_TIME, CLOSE_DATE_TIME, QRY_GAP, QRY_TP,
        INDEX_KEY. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/opt-monthly-ccnl",
            "HHDFO55020300",
            params=params or {},
        )

    async def search_opt_detail_hhdfo55200000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션 상품기본정보 [해외선물-041] -- GET
        /uapi/overseas-futureoption/v1/quotations/search-opt-detail (tr_id=HHDFO55200000). 필수:
        QRY_CNT, SRS_CD_01. 선택: 없음. 응답 컨테이너: output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/search-opt-detail",
            "HHDFO55200000",
            params=params or {},
        )

    async def opt_asking_price_hhdfo86000000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외옵션 호가 [해외선물-033] -- GET
        /uapi/overseas-futureoption/v1/quotations/opt-asking-price (tr_id=HHDFO86000000). 필수:
        SRS_CD. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/opt-asking-price",
            "HHDFO86000000",
            params=params or {},
        )

    async def inquire_deposit_otfm1411r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 예수금현황 [해외선물-012] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-deposit (tr_id=OTFM1411R). 필수: CANO,
        ACNT_PRDT_CD, CRCY_CD, INQR_DT. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-deposit",
            "OTFM1411R",
            params=params or {},
        )

    async def market_time_otfm2229r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 기본시세 > 해외선물옵션 장운영시간 [해외선물-030] -- GET
        /uapi/overseas-futureoption/v1/quotations/market-time (tr_id=OTFM2229R). 필수:
        FM_PDGR_CD, FM_CLAS_CD, FM_EXCG_CD, OPT_YN, CTX_AREA_NK200, CTX_AREA_FK200. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/market-time",
            "OTFM2229R",
            params=params or {},
        )

    @require_paper_sandbox
    async def order_rvsecncl_otfm3002u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 정정취소주문[v1_해외선물-002, 003] -- POST
        /uapi/overseas-futureoption/v1/trading/order-rvsecncl (tr_id=OTFM3002U). 필수: CANO,
        ACNT_PRDT_CD, ORGN_ORD_DT, ORGN_ODNO, FM_LIMIT_ORD_PRIC, FM_STOP_ORD_PRIC,
        FM_LQD_LMT_ORD_PRIC, FM_LQD_STOP_ORD_PRIC, FM_HDGE_ORD_SCRN_YN, FM_MKPR_CVSN_YN. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-futureoption/v1/trading/order-rvsecncl",
            "OTFM3002U",
            body=params or {},
        )

    async def inquire_period_trans_otfm3114r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 기간계좌거래내역 [해외선물-014] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-period-trans (tr_id=OTFM3114R). 필수:
        INQR_TERM_FROM_DT, INQR_TERM_TO_DT, CANO, ACNT_PRDT_CD, ACNT_TR_TYPE_CD, CRCY_CD,
        CTX_AREA_FK100, CTX_AREA_NK100, PWD_CHK_YN. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-period-trans",
            "OTFM3114R",
            params=params or {},
        )

    async def margin_detail_otfm3115r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 증거금상세 [해외선물-032] -- GET
        /uapi/overseas-futureoption/v1/trading/margin-detail (tr_id=OTFM3115R). 필수: CANO,
        ACNT_PRDT_CD, CRCY_CD, INQR_DT. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/margin-detail",
            "OTFM3115R",
            params=params or {},
        )

    async def inquire_ccld_otfm3116r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 당일주문내역조회 [v1_해외선물-004] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-ccld (tr_id=OTFM3116R). 필수: CANO,
        ACNT_PRDT_CD, CCLD_NCCS_DVSN, SLL_BUY_DVSN_CD, FUOP_DVSN, CTX_AREA_FK200,
        CTX_AREA_NK200. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-ccld",
            "OTFM3116R",
            params=params or {},
        )

    async def inquire_period_ccld_otfm3118r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 기간계좌손익 일별 [해외선물-010] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-period-ccld (tr_id=OTFM3118R). 필수:
        INQR_TERM_FROM_DT, INQR_TERM_TO_DT, CANO, ACNT_PRDT_CD, CRCY_CD, WHOL_TRSL_YN,
        FUOP_DVSN, CTX_AREA_FK200, CTX_AREA_NK200. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-period-ccld",
            "OTFM3118R",
            params=params or {},
        )
