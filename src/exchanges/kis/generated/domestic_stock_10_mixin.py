"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 10.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock10Mixin(_KISRestHost):

    async def ksdinfo_merger_split_hhkdb669104c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 예탁원정보(합병_분할일정)[국내주식-147] -- GET
        /uapi/domestic-stock/v1/ksdinfo/merger-split (tr_id=HHKDB669104C0). 필수: CTS, F_DT,
        T_DT, SHT_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/merger-split",
            "HHKDB669104C0",
            params=params or {},
        )

    async def ksdinfo_rev_split_hhkdb669105c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 예탁원정보(액면교체일정) -- GET
        /uapi/domestic-stock/v1/ksdinfo/rev-split (tr_id=HHKDB669105C0). 필수: SHT_CD, CTS,
        F_DT, T_DT, MARKET_GB. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/rev-split",
            "HHKDB669105C0",
            params=params or {},
        )

    async def ksdinfo_cap_dcrs_hhkdb669106c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 예탁원정보(자본감소일정) [국내주식-149] -- GET
        /uapi/domestic-stock/v1/ksdinfo/cap-dcrs (tr_id=HHKDB669106C0). 필수: CTS, F_DT, T_DT,
        SHT_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/cap-dcrs",
            "HHKDB669106C0",
            params=params or {},
        )

    async def ksdinfo_list_info_hhkdb669107c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 예탁원정보(상장정보일정)[국내주식-150] -- GET
        /uapi/domestic-stock/v1/ksdinfo/list-info (tr_id=HHKDB669107C0). 필수: SHT_CD, T_DT,
        F_DT, CTS. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/list-info",
            "HHKDB669107C0",
            params=params or {},
        )

    async def ksdinfo_pub_offer_hhkdb669108c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 예탁원정보(공모주청약일정) -- GET
        /uapi/domestic-stock/v1/ksdinfo/pub-offer (tr_id=HHKDB669108C0). 필수: SHT_CD, CTS,
        F_DT, T_DT. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/pub-offer",
            "HHKDB669108C0",
            params=params or {},
        )

    async def ksdinfo_forfeit_hhkdb669109c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 예탁원정보(실권주일정)[국내주식-152] -- GET
        /uapi/domestic-stock/v1/ksdinfo/forfeit (tr_id=HHKDB669109C0). 필수: SHT_CD, T_DT, F_DT,
        CTS. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/forfeit",
            "HHKDB669109C0",
            params=params or {},
        )

    async def ksdinfo_mand_deposit_hhkdb669110c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 예탁원정보(의무예치일정) [국내주식-153] -- GET
        /uapi/domestic-stock/v1/ksdinfo/mand-deposit (tr_id=HHKDB669110C0). 필수: T_DT, SHT_CD,
        F_DT, CTS. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/mand-deposit",
            "HHKDB669110C0",
            params=params or {},
        )

    async def ksdinfo_sharehld_meet_hhkdb669111c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 예탁원정보(주주총회일정) -- GET
        /uapi/domestic-stock/v1/ksdinfo/sharehld-meet (tr_id=HHKDB669111C0). 필수: CTS, F_DT,
        T_DT, SHT_CD. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/sharehld-meet",
            "HHKDB669111C0",
            params=params or {},
        )

    async def psearch_title_hhkst03900300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목조건검색 목록조회[국내주식-038] -- GET
        /uapi/domestic-stock/v1/quotations/psearch-title (tr_id=HHKST03900300). 필수: user_id.
        선택: 없음. 응답 컨테이너: output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/psearch-title",
            "HHKST03900300",
            params=params or {},
        )

    async def psearch_result_hhkst03900400(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목조건검색조회 [국내주식-039] -- GET
        /uapi/domestic-stock/v1/quotations/psearch-result (tr_id=HHKST03900400). 필수: user_id,
        seq. 선택: 없음. 응답 컨테이너: output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/psearch-result",
            "HHKST03900400",
            params=params or {},
        )

    async def estimate_perform_hhkst668300c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 국내주식 종목추정실적[국내주식-187] -- GET
        /uapi/domestic-stock/v1/quotations/estimate-perform (tr_id=HHKST668300C0). 필수: SHT_CD.
        선택: 없음. 응답 컨테이너: output1, output2, output3, output4.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/estimate-perform",
            "HHKST668300C0",
            params=params or {},
        )

    async def market_time_hhmcm000002c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내선물 영업일조회 [국내주식-160] -- GET
        /uapi/domestic-stock/v1/quotations/market-time (tr_id=HHMCM000002C0). 필수: 없음. 선택:
        없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/market-time",
            "HHMCM000002C0",
            params=params or {},
        )

    async def hts_top_view_hhmcm000100c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > HTS조회상위20종목[국내주식-214] -- GET
        /uapi/domestic-stock/v1/ranking/hts-top-view (tr_id=HHMCM000100C0). 필수: 없음. 선택:
        없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/hts-top-view",
            "HHMCM000100C0",
            params=params or {},
        )

    async def investor_program_trade_today_hhppg046600c1(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 프로그램매매 투자자매매동향(당일) [국내주식-116] -- GET
        /uapi/domestic-stock/v1/quotations/investor-program-trade-today (tr_id=HHPPG046600C1).
        필수: MRKT_DIV_CLS_CODE. 선택: 없음. 응답 컨테이너: output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/investor-program-trade-today",
            "HHPPG046600C1",
            params=params or {},
        )

    async def daily_loan_trans_hhpst074500c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목별 일별 대차거래추이 [국내주식-135] -- GET
        /uapi/domestic-stock/v1/quotations/daily-loan-trans (tr_id=HHPST074500C0). 필수:
        MRKT_DIV_CLS_CODE, MKSC_SHRN_ISCD. 선택: START_DATE, END_DATE, CTS. 응답 컨테이너:
        output1.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/daily-loan-trans",
            "HHPST074500C0",
            params=params or {},
        )

    async def inquire_daily_ccld_vtsc9215r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] -- GET
        /uapi/domestic-stock/v1/trading/inquire-daily-ccld (tr_id=VTSC9215R). 필수: CANO,
        ACNT_PRDT_CD, INQR_STRT_DT, INQR_END_DT, SLL_BUY_DVSN_CD, CCLD_DVSN, INQR_DVSN,
        INQR_DVSN_3. 선택: PDNO, ORD_GNO_BRNO, ODNO, INQR_DVSN_1, CTX_AREA_FK100,
        CTX_AREA_NK100. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "VTSC9215R",
            params=params or {},
        )

    async def order_cash_vttc0011u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001] -- POST
        /uapi/domestic-stock/v1/trading/order-cash (tr_id=VTTC0011U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, ORD_DVSN, ORD_QTY, ORD_UNPR, EXCG_ID_DVSN_CD. 선택: SLL_TYPE, CNDT_PRIC. 응답
        컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            "VTTC0011U",
            body=params or {},
        )
