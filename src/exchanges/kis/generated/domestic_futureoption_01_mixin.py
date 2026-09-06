"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_futureoption 미착수 TR 청크 01.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticFutureoption01Mixin(_KISRestHost):

    async def inquire_ngt_balance_ctfn6118r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > (야간)선물옵션 잔고현황 [국내선물-010] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-ngt-balance (tr_id=CTFN6118R). 필수:
        CANO, ACNT_PRDT_CD, MGNA_DVSN, EXCC_STAT_CD. 선택: ACNT_PWD, CTX_AREA_FK200,
        CTX_AREA_NK200. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-ngt-balance",
            "CTFN6118R",
            params=params or {},
        )

    async def ngt_margin_detail_ctfn7107r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > (야간)선물옵션 증거금 상세 [국내선물-024] -- GET
        /uapi/domestic-futureoption/v1/trading/ngt-margin-detail (tr_id=CTFN7107R). 필수: CANO,
        ACNT_PRDT_CD, MGNA_DVSN_CD. 선택: 없음. 응답 컨테이너: output1, output2, output3.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/ngt-margin-detail",
            "CTFN7107R",
            params=params or {},
        )

    async def inquire_ccnl_bstime_ctfo5139r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 기준일체결내역[v1_국내선물-016] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-ccnl-bstime (tr_id=CTFO5139R). 필수:
        CANO, ACNT_PRDT_CD, ORD_DT, FUOP_TR_STRT_TMD, FUOP_TR_END_TMD. 선택: CTX_AREA_FK200,
        CTX_AREA_NK200. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-ccnl-bstime",
            "CTFO5139R",
            params=params or {},
        )

    async def inquire_balance_settlement_pl_ctfo6117r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 잔고정산손익내역[v1_국내선물-013] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-balance-settlement-pl (tr_id=CTFO6117R).
        필수: CANO, ACNT_PRDT_CD, INQR_DT. 선택: CTX_AREA_FK200, CTX_AREA_NK200. 응답 컨테이너:
        output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-balance-settlement-pl",
            "CTFO6117R",
            params=params or {},
        )

    async def inquire_daily_amount_fee_ctfo6119r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션기간약정수수료일별[v1_국내선물-017] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-daily-amount-fee (tr_id=CTFO6119R). 필수:
        CANO, ACNT_PRDT_CD, INQR_STRT_DAY, INQR_END_DAY. 선택: CTX_AREA_FK200, CTX_AREA_NK200.
        응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-daily-amount-fee",
            "CTFO6119R",
            params=params or {},
        )

    async def inquire_balance_valuation_pl_ctfo6159r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 잔고평가손익내역[v1_국내선물-015] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-balance-valuation-pl (tr_id=CTFO6159R).
        필수: CANO, ACNT_PRDT_CD, MGNA_DVSN, EXCC_STAT_CD. 선택: CTX_AREA_FK200, CTX_AREA_NK200.
        응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-balance-valuation-pl",
            "CTFO6159R",
            params=params or {},
        )

    async def inquire_deposit_ctrp6550r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 주문/계좌 > 선물옵션 총자산현황[v1_국내선물-014] -- GET
        /uapi/domestic-futureoption/v1/trading/inquire-deposit (tr_id=CTRP6550R). 필수: CANO,
        ACNT_PRDT_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-deposit",
            "CTRP6550R",
            params=params or {},
        )

    async def inquire_daily_fuopchartprice_fhkif03020100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 선물옵션기간별시세(일/주/월/년)[v1_국내선물-008] -- GET
        /uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice
        (tr_id=FHKIF03020100). 필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1,
        FID_INPUT_DATE_2, FID_PERIOD_DIV_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice",
            "FHKIF03020100",
            params=params or {},
        )

    async def inquire_time_fuopchartprice_fhkif03020200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 선물옵션 분봉조회[v1_국내선물-012] -- GET
        /uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice
        (tr_id=FHKIF03020200). 필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_HOUR_CLS_CODE,
        FID_PW_DATA_INCU_YN, FID_FAKE_TICK_INCU_YN, FID_INPUT_DATE_1, FID_INPUT_HOUR_1. 선택:
        없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice",
            "FHKIF03020200",
            params=params or {},
        )

    async def inquire_asking_price_fhmif10010000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 선물옵션 시세호가[v1_국내선물-007] -- GET
        /uapi/domestic-futureoption/v1/quotations/inquire-asking-price (tr_id=FHMIF10010000).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price",
            "FHMIF10010000",
            params=params or {},
        )

    async def display_board_top_fhpif05030000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 국내선물 기초자산 시세[국내선물-021] -- GET
        /uapi/domestic-futureoption/v1/quotations/display-board-top (tr_id=FHPIF05030000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: FID_COND_MRKT_DIV_CODE1,
        FID_COND_SCR_DIV_CODE, FID_MTRT_CNT, FID_COND_MRKT_CLS_CODE. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/display-board-top",
            "FHPIF05030000",
            params=params or {},
        )

    async def display_board_callput_fhpif05030100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 국내옵션전광판_콜풋[국내선물-022] -- GET
        /uapi/domestic-futureoption/v1/quotations/display-board-callput (tr_id=FHPIF05030100).
        필수: FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_MRKT_CLS_CODE, FID_MTRT_CNT,
        FID_MRKT_CLS_CODE1. 선택: FID_COND_MRKT_CLS_CODE. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/display-board-callput",
            "FHPIF05030100",
            params=params or {},
        )

    async def display_board_futures_fhpif05030200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 국내옵션전광판_선물[국내선물-023] -- GET
        /uapi/domestic-futureoption/v1/quotations/display-board-futures (tr_id=FHPIF05030200).
        필수: FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_COND_MRKT_CLS_CODE. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/display-board-futures",
            "FHPIF05030200",
            params=params or {},
        )

    async def exp_price_trend_fhpif05110100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 선물옵션 일중예상체결추이[국내선물-018] -- GET
        /uapi/domestic-futureoption/v1/quotations/exp-price-trend (tr_id=FHPIF05110100). 필수:
        FID_INPUT_ISCD, FID_COND_MRKT_DIV_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/exp-price-trend",
            "FHPIF05110100",
            params=params or {},
        )

    async def display_board_option_list_fhpio056104c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내선물옵션] 기본시세 > 국내옵션전광판_옵션월물리스트[국내선물-020] -- GET
        /uapi/domestic-futureoption/v1/quotations/display-board-option-list
        (tr_id=FHPIO056104C0). 필수: FID_COND_SCR_DIV_CODE. 선택: FID_COND_MRKT_DIV_CODE,
        FID_COND_MRKT_CLS_CODE. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/display-board-option-list",
            "FHPIO056104C0",
            params=params or {},
        )
