"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 03.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock03Mixin(_KISRestHost):

    async def exp_total_index_fhkup11750000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 업종/기타 > 국내주식 예상체결 전체지수[국내주식-122] -- GET
        /uapi/domestic-stock/v1/quotations/exp-total-index (tr_id=FHKUP11750000). 필수:
        fid_mrkt_cls_code, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_mkop_cls_code. 선택: 없음. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/exp-total-index",
            "FHKUP11750000",
            params=params or {},
        )

    async def comp_program_trade_today_fhppg04600101(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 프로그램매매 종합현황(시간) [국내주식-114] -- GET
        /uapi/domestic-stock/v1/quotations/comp-program-trade-today (tr_id=FHPPG04600101). 필수:
        FID_COND_MRKT_DIV_CODE, FID_MRKT_CLS_CODE. 선택: FID_SCTN_CLS_CODE, FID_INPUT_ISCD,
        FID_COND_MRKT_DIV_CODE1, FID_INPUT_HOUR_1. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/comp-program-trade-today",
            "FHPPG04600101",
            params=params or {},
        )

    async def program_trade_by_stock_fhppg04650101(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목별 프로그램매매추이(체결)[v1_국내주식-044] -- GET
        /uapi/domestic-stock/v1/quotations/program-trade-by-stock (tr_id=FHPPG04650101). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/program-trade-by-stock",
            "FHPPG04650101",
            params=params or {},
        )

    async def program_trade_by_stock_daily_fhppg04650201(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 종목별 프로그램매매추이(일별) [국내주식-113] -- GET
        /uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily (tr_id=FHPPG04650201).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: FID_INPUT_DATE_1. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
            "FHPPG04650201",
            params=params or {},
        )

    async def inquire_price_2_fhpst01010000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 시세2[v1_국내주식-054] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-price-2 (tr_id=FHPST01010000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price-2",
            "FHPST01010000",
            params=params or {},
        )

    async def inquire_time_itemconclusion_fhpst01060000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 주식현재가 당일시간대별체결[v1_국내주식-023] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion (tr_id=FHPST01060000).
        필수: FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_INPUT_HOUR_1. 선택: 없음. 응답
        컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion",
            "FHPST01060000",
            params=params or {},
        )

    async def pbar_tratio_fhpst01130000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 시세분석 > 국내주식 매물대/거래비중 [국내주식-196] -- GET
        /uapi/domestic-stock/v1/quotations/pbar-tratio (tr_id=FHPST01130000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_COND_SCR_DIV_CODE. 선택: FID_INPUT_HOUR_1.
        응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/pbar-tratio",
            "FHPST01130000",
            params=params or {},
        )

    async def inquire_vi_status_fhpst01390000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 변동성완화장치(VI) 현황[v1_국내주식-055] -- GET
        /uapi/domestic-stock/v1/quotations/inquire-vi-status (tr_id=FHPST01390000). 필수:
        FID_DIV_CLS_CODE, FID_COND_SCR_DIV_CODE, FID_MRKT_CLS_CODE, FID_INPUT_ISCD,
        FID_RANK_SORT_CLS_CODE, FID_INPUT_DATE_1, FID_TRGT_CLS_CODE, FID_TRGT_EXLS_CLS_CODE.
        선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-vi-status",
            "FHPST01390000",
            params=params or {},
        )

    async def volume_power_fhpst01680000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 체결강도 상위[v1_국내주식-101] -- GET
        /uapi/domestic-stock/v1/ranking/volume-power (tr_id=FHPST01680000). 필수:
        fid_trgt_exls_cls_code, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_div_cls_code, fid_input_price_1, fid_input_price_2, fid_vol_cnt, fid_trgt_cls_code.
        선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/volume-power",
            "FHPST01680000",
            params=params or {},
        )

    async def fluctuation_fhpst01700000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 등락률 순위[v1_국내주식-088] -- GET
        /uapi/domestic-stock/v1/ranking/fluctuation (tr_id=FHPST01700000). 필수: fid_rsfl_rate2,
        fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd, fid_rank_sort_cls_code,
        fid_input_cnt_1, fid_prc_cls_code, fid_input_price_1, fid_input_price_2, fid_vol_cnt,
        fid_trgt_cls_code, fid_trgt_exls_cls_code, fid_div_cls_code, fid_rsfl_rate1. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/fluctuation",
            "FHPST01700000",
            params=params or {},
        )

    async def volume_rank_fhpst01710000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 거래량순위[v1_국내주식-047] -- GET
        /uapi/domestic-stock/v1/quotations/volume-rank (tr_id=FHPST01710000). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD, FID_DIV_CLS_CODE,
        FID_BLNG_CLS_CODE, FID_TRGT_CLS_CODE, FID_TRGT_EXLS_CLS_CODE, FID_INPUT_PRICE_1,
        FID_INPUT_PRICE_2, FID_VOL_CNT, FID_INPUT_DATE_1. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000",
            params=params or {},
        )

    async def quote_balance_fhpst01720000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 기본시세 > 국내주식 호가잔량 순위[국내주식-089] -- GET
        /uapi/domestic-stock/v1/ranking/quote-balance (tr_id=FHPST01720000). 필수: fid_vol_cnt,
        fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd, fid_rank_sort_cls_code,
        fid_div_cls_code, fid_trgt_cls_code, fid_trgt_exls_cls_code, fid_input_price_1,
        fid_input_price_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/quote-balance",
            "FHPST01720000",
            params=params or {},
        )

    async def profit_asset_index_fhpst01730000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 수익자산지표 순위[v1_국내주식-090] -- GET
        /uapi/domestic-stock/v1/ranking/profit-asset-index (tr_id=FHPST01730000). 필수:
        fid_cond_mrkt_div_code, fid_trgt_cls_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_div_cls_code, fid_input_price_1, fid_input_price_2, fid_vol_cnt, fid_input_option_1,
        fid_input_option_2, fid_rank_sort_cls_code, fid_blng_cls_code, fid_trgt_exls_cls_code.
        선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/profit-asset-index",
            "FHPST01730000",
            params=params or {},
        )

    async def market_cap_fhpst01740000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 시가총액 상위 [v1_국내주식-091] -- GET
        /uapi/domestic-stock/v1/ranking/market-cap (tr_id=FHPST01740000). 필수:
        fid_input_price_2, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_div_cls_code,
        fid_input_iscd, fid_trgt_cls_code, fid_trgt_exls_cls_code, fid_input_price_1,
        fid_vol_cnt. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/market-cap",
            "FHPST01740000",
            params=params or {},
        )

    async def finance_ratio_fhpst01750000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 순위분석 > 국내주식 재무비율 순위[v1_국내주식-092] -- GET
        /uapi/domestic-stock/v1/ranking/finance-ratio (tr_id=FHPST01750000). 필수:
        fid_trgt_cls_code, fid_cond_mrkt_div_code, fid_cond_scr_div_code, fid_input_iscd,
        fid_div_cls_code, fid_input_price_1, fid_input_price_2, fid_vol_cnt, fid_input_option_1,
        fid_input_option_2, fid_rank_sort_cls_code, fid_blng_cls_code, fid_trgt_exls_cls_code.
        선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/ranking/finance-ratio",
            "FHPST01750000",
            params=params or {},
        )
