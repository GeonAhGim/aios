"""02d_kis_api_full_spec_v1.md §4 — KISAdapter 국내채권(domestic_bond) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §4, §7(작업 분해 4번)

06번 §6.1-A(자산군 확장 원칙) 재확인 — Phase 1 확정 스콥은 KR_EQUITY뿐,
이 mixin은 사용자 요청("모든 기능")에 따른 API 연동만 제공한다.
`ExchangeAdapter` ABC에는 아직 없음(overseas_stock_mixin.py와 동일
원칙). tr_id/path는 공식 예제(github.com/koreainvestment/open-trading-
api/examples_llm/domestic_bond, 2026-09-02 WebFetch 확인) 그대로:
- GET  /uapi/domestic-bond/v1/quotations/inquire-price (FHKBJ773400C0)
- POST /uapi/domestic-bond/v1/trading/buy (TTTC0952U)
- POST /uapi/domestic-bond/v1/trading/sell (TTTC0958U)
- GET  /uapi/domestic-bond/v1/trading/inquire-balance (CTSC8407R)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus
from src.exchanges.common.live_guard import require_paper_sandbox

_MARKET_CODE = "B"  # 채권시장(공식 예제 확인)


class KISDomesticBondMixin:
    async def get_bond_price(self, bond_code: str) -> Ticker:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-bond/v1/quotations/inquire-price",
            "FHKBJ773400C0",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": bond_code},
        )
        output = raw.get("output", {})
        price = Decimal(output.get("bond_prpr", output.get("prpr", "0")))
        return Ticker(
            symbol=bond_code,
            exchange="kis",
            price=price,
            bid=price,  # 채권 현재가 응답엔 최우선호가가 없음(문서 관례) — 근사치
            ask=price,
            volume_24h=Decimal(output.get("acml_vol", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )

    @require_paper_sandbox
    async def place_bond_order(self, order: Order) -> Order:
        """`ORD_QTY2`(채권 전용 수량 필드명 — 주식의 `ORD_QTY`와 다름,
        공식 예제 확인)와 `BOND_ORD_UNPR`(채권 단가)을 쓴다."""
        tr_id = "TTTC0952U" if order.side == OrderSide.BUY else "TTTC0958U"
        path = (
            "/uapi/domestic-bond/v1/trading/buy"
            if order.side == OrderSide.BUY
            else "/uapi/domestic-bond/v1/trading/sell"
        )
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "PDNO": order.symbol,
            "ORD_QTY2": str(order.quantity),
            "BOND_ORD_UNPR": str(order.price.amount) if order.price is not None else "0",
            "SAMT_MKET_PTCI_YN": "N",
            "BOND_RTL_MKET_YN": "Y",
            "IDCR_STFNO": "",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0",
            "CTAC_TLNO": "",
        }
        raw = await self._request("POST", path, tr_id, body=body)  # type: ignore[attr-defined]
        output = raw.get("output", {})
        exchange_order_id = f"{output.get('KRX_FWDG_ORD_ORGNO', '')}:{output.get('ODNO', '')}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    async def get_bond_balance(self) -> list[AccountBalance]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-bond/v1/trading/inquire-balance",
            "CTSC8407R",
            params={
                "CANO": self._cano,  # type: ignore[attr-defined]
                "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
                "INQR_CNDT": "00",
                "PDNO": "",
                "BUY_DT": "",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
        balances = []
        for row in raw.get("output", []):
            qty = Decimal(row.get("bal_qty", row.get("cblc_qty", "0")))
            if qty == 0:
                continue
            balances.append(
                AccountBalance(
                    exchange="kis",
                    asset=row.get("pdno", ""),
                    total=qty,
                    available=qty,
                    used_margin=Decimal("0"),
                )
            )
        return balances
