"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 05.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock05Mixin(_KISRestHost):

    async def overtime_volume_fhpst02350000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 - 국내주식 시간외거래량순위 -- GET
        /uapi/domestic-stock/v1/ranking/overtime-volume (tr_id=FHPST02350000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_RANK_SORT_CLS_CODE,
        FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_VOL_CNT, FID_TRGT_CLS_CODE,
        FID_TRGT_EXLS_CLS_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/overtime-volume",
            "FHPST02350000",
            params=params or {},
        )

    async def frgnmem_trade_trend_fhpst04320000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 회원사 실 시간 매매동향(틱)[국내주식-163] -- GET
        /uapi/domestic-stock/v1/quotations/frgnmem-trade-trend (tr_id=FHPST04320000). 필수:
        FID_COND_SCR_DIV_CODE, FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_ISCD_2,
        FID_MRKT_CLS_CODE, FID_VOL_CNT. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/frgnmem-trade-trend",
            "FHPST04320000",
            params=params or {},
        )

    async def inquire_member_daily_fhpst04540000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 주식현재가 회원사 종목매매동향 [국내주식-197] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-member-daily (tr_id=FHPST04540000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_ISCD_2, FID_INPUT_DATE_1,
        FID_INPUT_DATE_2. 선택: FID_SCTN_CLS_CODE. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-member-daily",
            "FHPST04540000",
            params=params or {},
        )

    async def short_sale_fhpst04820000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 공매도 상위종목[국내주식-133] -- GET
        /uapi/domestic-stock/v1/ranking/short-sale (tr_id=FHPST04820000). 필수:
        FID_APLY_RANG_VOL, FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD,
        FID_PERIOD_DIV_CODE, FID_INPUT_CNT_1, FID_TRGT_EXLS_CLS_CODE, FID_TRGT_CLS_CODE,
        FID_APLY_RANG_PRC_1, FID_APLY_RANG_PRC_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/short-sale",
            "FHPST04820000",
            params=params or {},
        )

    async def daily_short_sale_fhpst04830000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내주식 공매도 일별추이[국내주식-134] -- GET
        /uapi/domestic-stock/v1/quotations/daily-short-sale (tr_id=FHPST04830000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: FID_INPUT_DATE_1, FID_INPUT_DATE_2. 응답
        컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/daily-short-sale",
            "FHPST04830000",
            params=params or {},
        )

    async def comp_interest_fhpst07020000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 금리 종합(국내채권_금리)[국내주식-155] -- GET
        /uapi/domestic-stock/v1/quotations/comp-interest (tr_id=FHPST07020000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_DIV_CLS_CODE, FID_DIV_CLS_CODE1.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/comp-interest",
            "FHPST07020000",
            params=params or {},
        )

    async def inquire_investor_time_by_market_fhptj04030000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 시장별 투자자매매동향(시세)[v1_국내주식-074] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market
        (tr_id=FHPTJ04030000). 필수: FID_INPUT_ISCD, FID_INPUT_ISCD_2. 선택: 없음. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
            "FHPTJ04030000",
            params=params or {},
        )

    async def inquire_investor_daily_by_market_fhptj04040000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 시장별 투자자매매동향(일별) [국내주식-075] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market
        (tr_id=FHPTJ04040000). 필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1,
        FID_INPUT_ISCD_1, FID_INPUT_DATE_2, FID_INPUT_ISCD_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
            "FHPTJ04040000",
            params=params or {},
        )

    async def investor_trade_by_stock_daily_fhptj04160001(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석  > 종목별 투자자매매동향(일별)[종목별 투자자매매동향(일별)] -- GET
        /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily (tr_id=FHPTJ04160001).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1, FID_ORG_ADJ_PRC,
        FID_ETC_CLS_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            "FHPTJ04160001",
            params=params or {},
        )

    async def foreign_institution_total_fhptj04400000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내기관_외국인 매매종목가집계[국내주식-037] -- GET
        /uapi/domestic-stock/v1/quotations/foreign-institution-total (tr_id=FHPTJ04400000).
        필수: FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_DIV_CLS_CODE,
        FID_RANK_SORT_CLS_CODE, FID_ETC_CLS_CODE. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            "FHPTJ04400000",
            params=params or {},
        )

    async def inquire_index_price_fhpup02100000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내업종 현재지수 [v1_국내주식-063] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-index-price (tr_id=FHPUP02100000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            params=params or {},
        )

    async def inquire_index_tickprice_fhpup02110100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내업종 시간별지수(초)[국내주식-064] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-index-tickprice (tr_id=FHPUP02110100). 필수:
        FID_INPUT_ISCD, FID_COND_MRKT_DIV_CODE. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-index-tickprice",
            "FHPUP02110100",
            params=params or {},
        )

    async def inquire_index_timeprice_fhpup02110200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내업종 시간별지수(분)[국내주식-119] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-index-timeprice (tr_id=FHPUP02110200). 필수:
        FID_INPUT_HOUR_1, FID_INPUT_ISCD, FID_COND_MRKT_DIV_CODE. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-index-timeprice",
            "FHPUP02110200",
            params=params or {},
        )

    async def inquire_index_daily_price_fhpup02120000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내업종 일자별지수 [v1_국내주식-065] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-index-daily-price (tr_id=FHPUP02120000).
        필수: FID_PERIOD_DIV_CODE, FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
            "FHPUP02120000",
            params=params or {},
        )

    async def inquire_index_category_price_fhpup02140000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내업종 구분별전체시세[v1_국내주식-066] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-index-category-price (tr_id=FHPUP02140000).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_COND_SCR_DIV_CODE, FID_MRKT_CLS_CODE,
        FID_BLNG_CLS_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-index-category-price",
            "FHPUP02140000",
            params=params or {},
        )
