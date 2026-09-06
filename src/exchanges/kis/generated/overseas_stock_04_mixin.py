"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- overseas_stock 미착수 TR 청크 04.

`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)
에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만
예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드
단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.
"""
from __future__ import annotations

from typing import Any

from src.exchanges.kis.generated._protocols import _KISRestHost


class KISGeneratedOverseasStock04Mixin(_KISRestHost):

    async def order_rvsecncl_vttt1004u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 정정취소주문[v1_해외주식-003] -- POST
        /uapi/overseas-stock/v1/trading/order-rvsecncl (tr_id=VTTT1004U). 필수: CANO,
        ACNT_PRDT_CD, OVRS_EXCG_CD, PDNO, ORGN_ODNO, RVSE_CNCL_DVSN_CD, ORD_QTY, OVRS_ORD_UNPR,
        MGCO_APTM_ODNO, ORD_SVR_DVSN_CD. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-rvsecncl",
            "VTTT1004U",
            body=params or {},
        )

    async def order_resv_vttt3014u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] -- POST
        /uapi/overseas-stock/v1/trading/order-resv (tr_id=VTTT3014U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, OVRS_EXCG_CD, FT_ORD_QTY, FT_ORD_UNPR3. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv",
            "VTTT3014U",
            body=params or {},
        )

    async def order_resv_vttt3016u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수[v1_해외주식-002] -- POST
        /uapi/overseas-stock/v1/trading/order-resv (tr_id=VTTT3016U). 필수: CANO, ACNT_PRDT_CD,
        PDNO, OVRS_EXCG_CD, FT_ORD_QTY, FT_ORD_UNPR3. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv",
            "VTTT3016U",
            body=params or {},
        )

    async def order_resv_ccnl_vttt3017u(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.
        [해외주식] 주문/계좌 > 해외주식 예약주문접수취소[v1_해외주식-004] -- POST
        /uapi/overseas-stock/v1/trading/order-resv-ccnl (tr_id=VTTT3017U). 필수: CANO,
        ACNT_PRDT_CD, RSVN_ORD_RCIT_DT, OVRS_RSVN_ODNO. 선택: 없음. 응답 컨테이너: output.
        """
        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order-resv-ccnl",
            "VTTT3017U",
            body=params or {},
        )
