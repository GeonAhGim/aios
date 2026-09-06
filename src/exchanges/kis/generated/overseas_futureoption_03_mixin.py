"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_futureoption 미착수 TR 청크 03.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedOverseasFutureoption03Mixin(_KISRestHost):

    async def inquire_daily_order_otfm3120r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 일별 주문내역 [해외선물-013] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-daily-order (tr_id=OTFM3120R). 필수:
        CANO, ACNT_PRDT_CD, STRT_DT, END_DT, FM_PDGR_CD, CCLD_NCCS_DVSN, SLL_BUY_DVSN_CD,
        FUOP_DVSN, CTX_AREA_FK200, CTX_AREA_NK200. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-daily-order",
            "OTFM3120R",
            params=params or {},
        )

    async def inquire_daily_ccld_otfm3122r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 일별체결내역[해외선물-011] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-daily-ccld (tr_id=OTFM3122R). 필수: CANO,
        ACNT_PRDT_CD, STRT_DT, END_DT, FUOP_DVSN_CD, FM_PDGR_CD, CRCY_CD, FM_ITEM_FTNG_YN,
        SLL_BUY_DVSN_CD, CTX_AREA_FK200, CTX_AREA_NK200. 선택: 없음. 응답 컨테이너: output1,
        output2.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-daily-ccld",
            "OTFM3122R",
            params=params or {},
        )

    async def inquire_psamount_otfm3304r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외선물옵션] 주문/계좌 > 해외선물옵션 주문가능조회 [v1_해외선물-006] -- GET
        /uapi/overseas-futureoption/v1/trading/inquire-psamount (tr_id=OTFM3304R). 필수: CANO,
        ACNT_PRDT_CD, OVRS_FUTR_FX_PDNO, SLL_BUY_DVSN_CD, FM_ORD_PRIC, ECIS_RSVN_ORD_YN. 선택:
        없음. 응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-psamount",
            "OTFM3304R",
            params=params or {},
        )
