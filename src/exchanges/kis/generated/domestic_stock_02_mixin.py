"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 02.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock02Mixin(_KISRestHost):

    async def intstock_multprice_fhkst11300006(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 관심종목(멀티종목) 시세조회 [국내주식-205] -- GET
        /uapi/domestic-stock/v1/quotations/intstock-multprice (tr_id=FHKST11300006). 필수:
        FID_COND_MRKT_DIV_CODE_1, FID_INPUT_ISCD_1. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/intstock-multprice",
            "FHKST11300006",
            params=params or {},
        )

    async def exp_closing_price_fhkst117300c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 국내주식 장마감 예상체결가[국내주식-120] -- GET
        /uapi/domestic-stock/v1/quotations/exp-closing-price (tr_id=FHKST117300C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_RANK_SORT_CLS_CODE, FID_COND_SCR_DIV_CODE,
        FID_BLNG_CLS_CODE. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/exp-closing-price",
            "FHKST117300C0",
            params=params or {},
        )

    async def overtime_exp_trans_fluct_fhkst11860000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내주식 시간외예상체결등락률 [국내주식-140] -- GET
        /uapi/domestic-stock/v1/ranking/overtime-exp-trans-fluct (tr_id=FHKST11860000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_RANK_SORT_CLS_CODE,
        FID_DIV_CLS_CODE. 선택: FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_INPUT_VOL_1. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/overtime-exp-trans-fluct",
            "FHKST11860000",
            params=params or {},
        )

    async def capture_uplowprice_fhkst130000c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내주식 상하한가 포착 [국내주식-190] -- GET
        /uapi/domestic-stock/v1/quotations/capture-uplowprice (tr_id=FHKST130000C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_PRC_CLS_CODE, FID_DIV_CLS_CODE,
        FID_INPUT_ISCD. 선택: FID_TRGT_CLS_CODE, FID_TRGT_EXLS_CLS_CODE, FID_INPUT_PRICE_1,
        FID_INPUT_PRICE_2, FID_VOL_CNT. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/capture-uplowprice",
            "FHKST130000C0",
            params=params or {},
        )

    async def bulk_trans_num_fhkst190900c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 대량체결건수 상위[국내주식-107] -- GET
        /uapi/domestic-stock/v1/ranking/bulk-trans-num (tr_id=FHKST190900C0). 필수:
        fid_aply_rang_prc_2, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_rank_sort_cls_code, fid_div_cls_code, fid_input_price_1, fid_aply_rang_prc_1,
        fid_input_iscd_2, fid_trgt_exls_cls_code, fid_trgt_cls_code, fid_vol_cnt. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/bulk-trans-num",
            "FHKST190900C0",
            params=params or {},
        )

    async def frgnmem_trade_estimate_fhkst644100c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 외국계 매매종목 가집계 [국내주식-161] -- GET
        /uapi/domestic-stock/v1/quotations/frgnmem-trade-estimate (tr_id=FHKST644100C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_RANK_SORT_CLS_CODE,
        FID_RANK_SORT_CLS_CODE_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/frgnmem-trade-estimate",
            "FHKST644100C0",
            params=params or {},
        )

    async def frgnmem_pchs_trend_fhkst644400c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목별 외국계 순매수추이 [국내주식-164] -- GET
        /uapi/domestic-stock/v1/quotations/frgnmem-pchs-trend (tr_id=FHKST644400C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_ISCD_2. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/frgnmem-pchs-trend",
            "FHKST644400C0",
            params=params or {},
        )

    async def mktfunds_fhkst649100c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내 증시자금 종합 [국내주식-193] -- GET
        /uapi/domestic-stock/v1/quotations/mktfunds (tr_id=FHKST649100C0). 필수: 없음. 선택:
        FID_INPUT_DATE_1. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/mktfunds",
            "FHKST649100C0",
            params=params or {},
        )

    async def invest_opinion_fhkst663300c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 국내주식 종목투자의견 -- GET
        /uapi/domestic-stock/v1/quotations/invest-opinion (tr_id=FHKST663300C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1,
        FID_INPUT_DATE_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/invest-opinion",
            "FHKST663300C0",
            params=params or {},
        )

    async def invest_opbysec_fhkst663400c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 국내주식 증권사별 투자의견 -- GET
        /uapi/domestic-stock/v1/quotations/invest-opbysec (tr_id=FHKST663400C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_DIV_CLS_CODE,
        FID_INPUT_DATE_1, FID_INPUT_DATE_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/invest-opbysec",
            "FHKST663400C0",
            params=params or {},
        )

    async def finance_profit_ratio_fhkst66430400(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 - 국내주식 수익성비율 -- GET
        /uapi/domestic-stock/v1/finance/profit-ratio (tr_id=FHKST66430400). 필수:
        fid_input_iscd, FID_DIV_CLS_CODE, fid_cond_mrkt_div_code. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/finance/profit-ratio",
            "FHKST66430400",
            params=params or {},
        )

    async def finance_other_major_ratios_fhkst66430500(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 국내주식 기타주요비율[v1_국내주식-082] -- GET
        /uapi/domestic-stock/v1/finance/other-major-ratios (tr_id=FHKST66430500). 필수:
        fid_input_iscd, fid_div_cls_code, fid_cond_mrkt_div_code. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/finance/other-major-ratios",
            "FHKST66430500",
            params=params or {},
        )

    async def finance_stability_ratio_fhkst66430600(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 국내주식 안정성비율[v1_국내주식-083] -- GET
        /uapi/domestic-stock/v1/finance/stability-ratio (tr_id=FHKST66430600). 필수:
        fid_input_iscd, fid_div_cls_code, fid_cond_mrkt_div_code. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/finance/stability-ratio",
            "FHKST66430600",
            params=params or {},
        )

    async def finance_growth_ratio_fhkst66430800(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 종목정보 > 국내주식 성장성비율 [v1_국내주식-085] -- GET
        /uapi/domestic-stock/v1/finance/growth-ratio (tr_id=FHKST66430800). 필수:
        fid_input_iscd, fid_div_cls_code, fid_cond_mrkt_div_code. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/finance/growth-ratio",
            "FHKST66430800",
            params=params or {},
        )

    async def inquire_daily_indexchartprice_fhkup03500100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 - 국내주식업종기간별시세(일/주/월/년) -- GET
        /uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice (tr_id=FHKUP03500100).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_DATE_1, FID_INPUT_DATE_2,
        FID_PERIOD_DIV_CODE. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
            "FHKUP03500100",
            params=params or {},
        )

    async def inquire_time_indexchartprice_fhkup03500200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 - 업종 분봉조회 -- GET
        /uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice (tr_id=FHKUP03500200).
        필수: FID_COND_MRKT_DIV_CODE, FID_ETC_CLS_CODE, FID_INPUT_ISCD, FID_INPUT_HOUR_1,
        FID_PW_DATA_INCU_YN. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice",
            "FHKUP03500200",
            params=params or {},
        )
