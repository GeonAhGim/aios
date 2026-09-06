"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- elw 미착수 TR 청크 01.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedElw01Mixin(_KISRestHost):

    async def cond_search_fhkew15100000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 종목검색[국내주식-166] -- GET
        /uapi/elw/v1/quotations/cond-search (tr_id=FHKEW15100000). 필수: FID_COND_MRKT_DIV_CODE,
        FID_COND_SCR_DIV_CODE, FID_RANK_SORT_CLS_CODE, FID_INPUT_CNT_1. 선택:
        FID_RANK_SORT_CLS_CODE_2, FID_INPUT_CNT_2, FID_RANK_SORT_CLS_CODE_3, FID_INPUT_CNT_3,
        FID_TRGT_CLS_CODE, FID_INPUT_ISCD, FID_UNAS_INPUT_ISCD, FID_MRKT_CLS_CODE,
        FID_INPUT_DATE_1, FID_INPUT_DATE_2, FID_INPUT_ISCD_2, FID_ETC_CLS_CODE,
        FID_INPUT_RMNN_DYNU_1, FID_INPUT_RMNN_DYNU_2, FID_PRPR_CNT1, FID_PRPR_CNT2,
        FID_RSFL_RATE1, FID_RSFL_RATE2, FID_VOL1, FID_VOL2, FID_APLY_RANG_PRC_1,
        FID_APLY_RANG_PRC_2, FID_LVRG_VAL1, FID_LVRG_VAL2, FID_VOL3, FID_VOL4, FID_INTS_VLTL1,
        FID_INTS_VLTL2, FID_PRMM_VAL1, FID_PRMM_VAL2, FID_GEAR1, FID_GEAR2, FID_PRLS_QRYR_RATE1,
        FID_PRLS_QRYR_RATE2, FID_DELTA1, FID_DELTA2, FID_ACPR1, FID_ACPR2, FID_STCK_CNVR_RATE1,
        FID_STCK_CNVR_RATE2, FID_DIV_CLS_CODE, FID_PRIT1, FID_PRIT2, FID_CFP1, FID_CFP2,
        FID_INPUT_NMIX_PRICE_1, FID_INPUT_NMIX_PRICE_2, FID_EGEA_VAL1, FID_EGEA_VAL2,
        FID_INPUT_DVDN_ERT, FID_INPUT_HIST_VLTL, FID_THETA1, FID_THETA2. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/cond-search",
            "FHKEW15100000",
            params=params or {},
        )

    async def compare_stocks_fhkew151701c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 비교대상종목조회[국내주식-183] -- GET
        /uapi/elw/v1/quotations/compare-stocks (tr_id=FHKEW151701C0). 필수:
        FID_COND_SCR_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/compare-stocks",
            "FHKEW151701C0",
            params=params or {},
        )

    async def udrl_asset_list_fhkew154100c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 기초자산 목록조회[국내주식-185] -- GET
        /uapi/elw/v1/quotations/udrl-asset-list (tr_id=FHKEW154100C0). 필수:
        FID_COND_SCR_DIV_CODE, FID_RANK_SORT_CLS_CODE, FID_INPUT_ISCD. 선택: 없음. 응답
        컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/udrl-asset-list",
            "FHKEW154100C0",
            params=params or {},
        )

    async def udrl_asset_price_fhkew154101c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 기초자산별 종목시세 -- GET
        /uapi/elw/v1/quotations/udrl-asset-price (tr_id=FHKEW154101C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_MRKT_CLS_CODE, FID_INPUT_ISCD,
        FID_UNAS_INPUT_ISCD, FID_VOL_CNT, FID_TRGT_EXLS_CLS_CODE, FID_INPUT_PRICE_1,
        FID_INPUT_PRICE_2, FID_INPUT_VOL_1, FID_INPUT_VOL_2, FID_INPUT_RMNN_DYNU_1,
        FID_INPUT_RMNN_DYNU_2, FID_OPTION, FID_INPUT_OPTION_1, FID_INPUT_OPTION_2. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/udrl-asset-price",
            "FHKEW154101C0",
            params=params or {},
        )

    async def expiration_stocks_fhkew154700c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 만기예정/만기종목[국내주식-184] -- GET
        /uapi/elw/v1/quotations/expiration-stocks (tr_id=FHKEW154700C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_INPUT_DATE_1, FID_INPUT_DATE_2,
        FID_DIV_CLS_CODE, FID_ETC_CLS_CODE, FID_UNAS_INPUT_ISCD, FID_INPUT_ISCD_2,
        FID_BLNG_CLS_CODE, FID_INPUT_OPTION_1. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/expiration-stocks",
            "FHKEW154700C0",
            params=params or {},
        )

    async def newly_listed_fhkew154800c0(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 신규상장종목[국내주식-181] -- GET
        /uapi/elw/v1/quotations/newly-listed (tr_id=FHKEW154800C0). 필수:
        FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE, FID_DIV_CLS_CODE, FID_UNAS_INPUT_ISCD,
        FID_INPUT_ISCD_2, FID_INPUT_DATE_1, FID_BLNG_CLS_CODE. 선택: 없음. 응답 컨테이너:
        output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/newly-listed",
            "FHKEW154800C0",
            params=params or {},
        )

    async def indicator_trend_ccnl_fhpew02740100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 투자지표추이(체결)[국내주식-172] -- GET
        /uapi/elw/v1/quotations/indicator-trend-ccnl (tr_id=FHPEW02740100). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/indicator-trend-ccnl",
            "FHPEW02740100",
            params=params or {},
        )

    async def indicator_trend_daily_fhpew02740200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 투자지표추이(일별)[국내주식-173] -- GET
        /uapi/elw/v1/quotations/indicator-trend-daily (tr_id=FHPEW02740200). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/indicator-trend-daily",
            "FHPEW02740200",
            params=params or {},
        )

    async def indicator_trend_minute_fhpew02740300(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 투자지표추이(분별)[국내주식-174] -- GET
        /uapi/elw/v1/quotations/indicator-trend-minute (tr_id=FHPEW02740300). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD, FID_HOUR_CLS_CODE, FID_PW_DATA_INCU_YN. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/indicator-trend-minute",
            "FHPEW02740300",
            params=params or {},
        )

    async def updown_rate_fhpew02770000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 상승률순위[국내주식-167] -- GET
        /uapi/elw/v1/ranking/updown-rate (tr_id=FHPEW02770000). 필수: FID_COND_MRKT_DIV_CODE,
        FID_COND_SCR_DIV_CODE, FID_UNAS_INPUT_ISCD, FID_INPUT_ISCD, FID_INPUT_RMNN_DYNU_1,
        FID_DIV_CLS_CODE, FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_INPUT_VOL_1,
        FID_INPUT_VOL_2, FID_INPUT_DATE_1, FID_RANK_SORT_CLS_CODE, FID_BLNG_CLS_CODE,
        FID_INPUT_DATE_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/ranking/updown-rate",
            "FHPEW02770000",
            params=params or {},
        )

    async def volume_rank_fhpew02780000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 거래량순위[국내주식-168] -- GET
        /uapi/elw/v1/ranking/volume-rank (tr_id=FHPEW02780000). 필수: FID_COND_MRKT_DIV_CODE,
        FID_COND_SCR_DIV_CODE, FID_UNAS_INPUT_ISCD, FID_INPUT_ISCD, FID_INPUT_RMNN_DYNU_1,
        FID_DIV_CLS_CODE, FID_INPUT_PRICE_1, FID_INPUT_PRICE_2, FID_INPUT_VOL_1,
        FID_INPUT_VOL_2, FID_INPUT_DATE_1, FID_RANK_SORT_CLS_CODE, FID_BLNG_CLS_CODE,
        FID_INPUT_ISCD_2, FID_INPUT_DATE_2. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/ranking/volume-rank",
            "FHPEW02780000",
            params=params or {},
        )

    async def indicator_fhpew02790000(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 지표순위[국내주식-169] -- GET /uapi/elw/v1/ranking/indicator
        (tr_id=FHPEW02790000). 필수: FID_COND_MRKT_DIV_CODE, FID_COND_SCR_DIV_CODE,
        FID_UNAS_INPUT_ISCD, FID_INPUT_ISCD, FID_DIV_CLS_CODE, FID_INPUT_PRICE_1,
        FID_INPUT_PRICE_2, FID_INPUT_VOL_1, FID_INPUT_VOL_2, FID_RANK_SORT_CLS_CODE,
        FID_BLNG_CLS_CODE. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/ranking/indicator",
            "FHPEW02790000",
            params=params or {},
        )

    async def sensitivity_trend_ccnl_fhpew02830100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 민감도 추이(체결)[국내주식-175] -- GET
        /uapi/elw/v1/quotations/sensitivity-trend-ccnl (tr_id=FHPEW02830100). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/sensitivity-trend-ccnl",
            "FHPEW02830100",
            params=params or {},
        )

    async def sensitivity_trend_daily_fhpew02830200(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 민감도 추이(일별)[국내주식-176] -- GET
        /uapi/elw/v1/quotations/sensitivity-trend-daily (tr_id=FHPEW02830200). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/sensitivity-trend-daily",
            "FHPEW02830200",
            params=params or {},
        )

    async def volatility_trend_ccnl_fhpew02840100(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] ELW시세 - ELW 변동성추이(체결)[국내주식-177] -- GET
        /uapi/elw/v1/quotations/volatility-trend-ccnl (tr_id=FHPEW02840100). 필수:
        FID_COND_MRKT_DIV_CODE, FID_INPUT_ISCD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/elw/v1/quotations/volatility-trend-ccnl",
            "FHPEW02840100",
            params=params or {},
        )
