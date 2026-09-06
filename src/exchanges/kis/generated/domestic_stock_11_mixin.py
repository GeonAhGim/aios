"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- domestic_stock 미착수 TR 청크 11.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedDomesticStock11Mixin(_KISRestHost):

    async def order_cash_vttc0012u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식주문(현금)[v1_국내주식-001] -- POST
        /uapi/domestic-stock/v1/trading/order-cash (tr_id=VTTC0012U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, ORD_DVSN, ORD_QTY, ORD_UNPR, EXCG_ID_DVSN_CD. 선택: SLL_TYPE, CNDT_PRIC. 응답
        컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            "VTTC0012U",
            body=params or {},
        )

    async def order_rvsecncl_vttc0013u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식주문(정정취소)[v1_국내주식-003] -- POST
        /uapi/domestic-stock/v1/trading/order-rvsecncl (tr_id=VTTC0013U). 필수: CANO,
        ACNT_PRDT_CD, KRX_FWDG_ORD_ORGNO, ORGN_ODNO, ORD_DVSN, RVSE_CNCL_DVSN_CD, ORD_QTY,
        ORD_UNPR, QTY_ALL_ORD_YN, EXCG_ID_DVSN_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            "VTTC0013U",
            body=params or {},
        )

    async def inquire_daily_ccld_vttc0081r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 주식일별주문체결조회[v1_국내주식-005] -- GET
        /uapi/domestic-stock/v1/trading/inquire-daily-ccld (tr_id=VTTC0081R). 필수: CANO,
        ACNT_PRDT_CD, INQR_STRT_DT, INQR_END_DT, SLL_BUY_DVSN_CD, CCLD_DVSN, INQR_DVSN,
        INQR_DVSN_3. 선택: PDNO, ORD_GNO_BRNO, ODNO, INQR_DVSN_1, CTX_AREA_FK100,
        CTX_AREA_NK100. 응답 컨테이너: output1, output2.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "VTTC0081R",
            params=params or {},
        )

    async def inquire_psbl_order_vttc8908r(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [국내주식] 주문/계좌 > 매수가능조회[v1_국내주식-007] -- GET
        /uapi/domestic-stock/v1/trading/inquire-psbl-order (tr_id=VTTC8908R). 필수: CANO,
        ACNT_PRDT_CD, PDNO, ORD_UNPR, ORD_DVSN, CMA_EVLU_AMT_ICLD_YN, OVRS_ICLD_YN. 선택: 없음.
        응답 컨테이너: output.
        """
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            "VTTC8908R",
            params=params or {},
        )
