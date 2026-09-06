"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 04.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock04Mixin(_KISRestHost):

    async def after_hour_balance_fhpst01760000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 국내주식 시간외잔량 순위[v1_국내주식-093] -- GET
        /uapi/domestic-stock/v1/ranking/after-hour-balance (tr_id=FHPST01760000). 필수:
        fid_input_price_1, fid_cond_mrkt_div_code, fid_cond_scr_div_code,
        fid_rank_sort_cls_code, fid_div_cls_code, fid_input_iscd, fid_trgt_exls_cls_code,
        fid_trgt_cls_code, fid_vol_cnt, fid_input_price_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/after-hour-balance",
            "FHPST01760000",
            params=params or {},
        )

    async def prefer_disparate_ratio_fhpst01770000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 우선주_괴리율 상위[v1_국내주식-094] -- GET
        /uapi/domestic-stock/v1/ranking/prefer-disparate-ratio (tr_id=FHPST01770000). 필수:
        fid_vol_cnt, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_div_cls_code,
        fid_input_iscd, fid_trgt_cls_code, fid_trgt_exls_cls_code, fid_input_price_1,
        fid_input_price_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/prefer-disparate-ratio",
            "FHPST01770000",
            params=params or {},
        )

    async def disparity_fhpst01780000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 이격도 순위 [v1_국내주식-095] -- GET
        /uapi/domestic-stock/v1/ranking/disparity (tr_id=FHPST01780000). 필수:
        fid_input_price_2, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_div_cls_code,
        fid_rank_sort_cls_code, fid_hour_cls_code, fid_input_iscd, fid_trgt_cls_code,
        fid_trgt_exls_cls_code, fid_input_price_1, fid_vol_cnt. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/disparity",
            "FHPST01780000",
            params=params or {},
        )

    async def market_value_fhpst01790000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 시장가치 순위[v1_국내주식-096] -- GET
        /uapi/domestic-stock/v1/ranking/market-value (tr_id=FHPST01790000). 필수:
        fid_trgt_cls_code, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_div_cls_code, fid_input_price_1, fid_input_price_2, fid_vol_cnt, fid_input_option_1,
        fid_input_option_2, fid_rank_sort_cls_code, fid_blng_cls_code, fid_trgt_exls_cls_code.
        선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/market-value",
            "FHPST01790000",
            params=params or {},
        )

    async def top_interest_stock_fhpst01800000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 관심종목등록 상위[v1_국내주식-102] -- GET
        /uapi/domestic-stock/v1/ranking/top-interest-stock (tr_id=FHPST01800000). 필수:
        fid_input_iscd_2, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_trgt_cls_code, fid_trgt_exls_cls_code, fid_input_price_1, fid_input_price_2,
        fid_vol_cnt, fid_div_cls_code, fid_input_cnt_1. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/top-interest-stock",
            "FHPST01800000",
            params=params or {},
        )

    async def exp_price_trend_fhpst01810000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내주식 예상체결가 추이[국내주식-118] -- GET
        /uapi/domestic-stock/v1/quotations/exp-price-trend (tr_id=FHPST01810000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_MKOP_CLS_CODE. 선택: 없음. 응답 컨테이너:
        output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/exp-price-trend",
            "FHPST01810000",
            params=params or {},
        )

    async def exp_trans_updown_fhpst01820000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 예상체결 상승_하락상위[v1_국내주식-103] -- GET
        /uapi/domestic-stock/v1/ranking/exp-trans-updown (tr_id=FHPST01820000). 필수:
        fid_rank_sort_cls_code, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_div_cls_code, fid_aply_rang_prc_1, fid_vol_cnt, fid_pbmn, fid_blng_cls_code,
        fid_mkop_cls_code. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/exp-trans-updown",
            "FHPST01820000",
            params=params or {},
        )

    async def exp_index_trend_fhpst01840000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내주식 예상체결지수 추이[국내주식-121] -- GET
        /uapi/domestic-stock/v1/quotations/exp-index-trend (tr_id=FHPST01840000). 필수:
        FID_MKOP_CLS_CODE, FID_INPUT_HOUR_1, FID_INPUT_ISCD, FID_COND_MRKT_DIV_CODE. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/exp-index-trend",
            "FHPST01840000",
            params=params or {},
        )

    async def traded_by_company_fhpst01860000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 당사매매종목 상위[v1_국내주식-104] -- GET
        /uapi/domestic-stock/v1/ranking/traded-by-company (tr_id=FHPST01860000). 필수:
        fid_trgt_exls_cls_code, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_div_cls_code,
        fid_rank_sort_cls_code, fid_input_date_1, fid_input_date_2, fid_input_iscd,
        fid_trgt_cls_code, fid_aply_rang_vol, fid_aply_rang_prc_2, fid_aply_rang_prc_1. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/traded-by-company",
            "FHPST01860000",
            params=params or {},
        )

    async def near_new_highlow_fhpst01870000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 신고_신저근접종목 상위[v1_국내주식-105] -- GET
        /uapi/domestic-stock/v1/ranking/near-new-highlow (tr_id=FHPST01870000). 필수:
        fid_aply_rang_vol, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_div_cls_code,
        fid_input_cnt_1, fid_input_cnt_2, fid_prc_cls_code, fid_input_iscd, fid_trgt_cls_code,
        fid_trgt_exls_cls_code, fid_aply_rang_prc_1, fid_aply_rang_prc_2. 선택: 없음. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/near-new-highlow",
            "FHPST01870000",
            params=params or {},
        )

    async def inquire_overtime_price_fhpst02300000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 국내주식 시간외현재가[국내주식-076] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-overtime-price (tr_id=FHPST02300000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-overtime-price",
            "FHPST02300000",
            params=params or {},
        )

    async def inquire_overtime_asking_price_fhpst02300400(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 국내주식 시간외호가[국내주식-077] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-overtime-asking-price (tr_id=FHPST02300400).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-overtime-asking-price",
            "FHPST02300400",
            params=params or {},
        )

    async def inquire_time_overtimeconclusion_fhpst02310000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 시간외시간별체결[v1_국내주식-025] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion
        (tr_id=FHPST02310000). 필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_HOUR_CLS_CODE.
        선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion",
            "FHPST02310000",
            params=params or {},
        )

    async def inquire_daily_overtimeprice_fhpst02320000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 시간외일자별주가[v1_국내주식-026] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice (tr_id=FHPST02320000).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice",
            "FHPST02320000",
            params=params or {},
        )

    async def overtime_fluctuation_fhpst02340000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 시간외등락율순위[국내주식-138] -- GET
        /uapi/domestic-stock/v1/ranking/overtime-fluctuation (tr_id=FHPST02340000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_MRKT_CLS_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD,
        FID_DIV_CLS_CODE, FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_VOL_CNT, FID_TRGT_CLS_CODE,
        FID_TRGT_EXLS_CLS_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/overtime-fluctuation",
            "FHPST02340000",
            params=params or {},
        )
