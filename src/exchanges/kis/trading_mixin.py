"""6.9/6.10 — KISAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트(2026-08-28 KIS 공식 GitHub 예제 소스코드 확인):
- POST /uapi/domestic-stock/v1/trading/order-cash, tr_id TTTC0012U(매수)/
  TTTC0011U(매도) — 모의투자 치환은 어댑터가 자동 처리
- POST /uapi/domestic-stock/v1/trading/order-rvsecncl (정정·취소 통합),
  RVSE_CNCL_DVSN_CD로 구분("01"=정정, "02"=취소)
- GET  /uapi/domestic-stock/v1/trading/inquire-daily-ccld, tr_id TTTC0081R
  (최근 3개월 이내 체결조회)

편차: KIS는 주문 취소/정정 시 KRX_FWDG_ORD_ORGNO(거래소전송주문조직번호)와
ORGN_ODNO(원주문번호)가 모두 필요하지만 Order.exchange_order_id는 단일
문자열이다 — place_order()가 "{orgno}:{odno}" 형식으로 합쳐 저장하고,
cancel_order/modify_order가 그 형식을 기대한다(문서화된 편의 규약).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType

_EXCHANGE_ID = "KRX"  # Phase 1 대상(06번 §6.1)


def _order_division(order_type: OrderType) -> str:
    return "01" if order_type == OrderType.MARKET else "00"


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"KIS exchange_order_id는 'orgno:odno' 형식이어야 함: {exchange_order_id}"
        )
    orgno, odno = exchange_order_id.split(":", 1)
    return orgno, odno


class KISTradingMixin:
    async def place_order(self, order: Order) -> Order:
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "PDNO": order.symbol,
            "ORD_DVSN": _order_division(order.order_type),
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(order.price.amount) if order.price is not None else "0",
            "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            "SLL_TYPE": "01" if order.side == OrderSide.SELL else "",
            "CNDT_PRIC": "",
        }
        tr_id = "TTTC0012U" if order.side == OrderSide.BUY else "TTTC0011U"
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body=body
        )
        output = raw["output"]
        exchange_order_id = f"{output['KRX_FWDG_ORD_ORGNO']}:{output['ODNO']}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    async def _rvsecncl(
        self, order_id: str, *, decision: str, quantity: Decimal | None
    ) -> dict[str, Any]:
        orgno, odno = _split_exchange_order_id(order_id)
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "KRX_FWDG_ORD_ORGNO": orgno,
            "ORGN_ODNO": odno,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": decision,
            "ORD_QTY": str(quantity) if quantity is not None else "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "N" if quantity is not None else "Y",
            "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
        }
        return await self._request(  # type: ignore[attr-defined,no-any-return]
            "POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", "TTTC0013U", body=body
        )

    async def cancel_order(self, order_id: str) -> bool:
        raw = await self._rvsecncl(order_id, decision="02", quantity=None)
        return bool(raw.get("rt_cd") == "0")

    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:
        quantity = kwargs.get("quantity")
        await self._rvsecncl(order_id, decision="01", quantity=quantity)
        return await self.get_order(order_id)

    async def get_order(self, order_id: str) -> Order:
        """편차: BitgetAdapter.get_order()와 동일 이유로 AIOS 전용 필드
        (strategy_id 등)는 자리표시자 — 호출부가 DB 행과 병합해야 한다."""
        _, odno = _split_exchange_order_id(order_id)
        today = datetime.now(timezone.utc)
        start = (today - timedelta(days=7)).strftime("%Y%m%d")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "TTTC0081R",
            params={
                "CANO": self._cano,  # type: ignore[attr-defined]
                "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
                "INQR_STRT_DT": start,
                "INQR_END_DT": today.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": odno,
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            },
        )
        rows = raw.get("output1", [])
        if not rows:
            raise FatalExchangeError(f"KIS 주문을 찾을 수 없음: order_id={order_id}")
        row = rows[0]

        filled_qty = Decimal(row.get("tot_ccld_qty", "0"))
        ord_qty = Decimal(row.get("ord_qty", "0"))
        if filled_qty == 0:
            status = OrderStatus.ACKNOWLEDGED
        elif filled_qty < ord_qty:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.FILLED

        return Order(
            order_id=uuid4(),
            exchange_order_id=order_id,
            client_order_id="",  # KIS는 client_order_id 개념이 없음(어댑터 docstring 참조)
            strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
            strategy_version="",
            symbol=row.get("pdno", ""),
            exchange="kis",
            side=OrderSide.BUY if row.get("sll_buy_dvsn_cd") == "02" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=ord_qty,
            status=status,
            filled_quantity=filled_qty,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            asset_class=AssetClass.KR_EQUITY,
        )

    async def health_check(self) -> bool:
        try:
            await self.get_balance()  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
