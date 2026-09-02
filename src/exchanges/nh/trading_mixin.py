"""NHAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§3

확인된 엔드포인트(2026-09-03 공식 SDK 소스코드 확인):
- POST /krstock/order/v1/cashBuy — body {act_no, iem_cd, orr_qty,
  orr_pr, nmn_pr_tp_cd:"01"(지정가)|"05"(시장가), orr_cnd_dit_cd,
  ssl_nmn_pr_dit_cd, rmt_mkt_cd:"KRX", sor_mkt_sli_yn}
- POST /krstock/order/v1/cashSell — cashBuy와 동일 파라미터 관례로 추정
  (매도 전용 필드가 있을 수 있으나 이번 조사에서 확인 못함)

**추정 엔드포인트**(공식 예제 없음 — cashBuy/cashSell·inquiry/v1/balance
명명 관례를 그대로 연장, PM 배정 지침 (2): 파싱 실패 시 조용히 기본값을
채우지 않고 FatalExchangeError로 실패):
- POST /krstock/order/v1/cashModify (정정)
- POST /krstock/order/v1/cashCancel (취소)
- POST /krstock/inquiry/v1/orderHistory (주문조회)

응답의 주문번호 필드명도 SDK 스니펫에 없었다 — `odno`(KIS 관례)를
최선 추정치로 사용, 없으면 즉시 실패한다(조용한 폴백 금지).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.live_guard import require_paper_sandbox

_MARKET_CODE = "KRX"


def _order_division(order_type: OrderType) -> str:
    return "05" if order_type == OrderType.MARKET else "01"


class NHTradingMixin:
    @require_paper_sandbox
    async def place_order(self, order: Order) -> Order:
        path = "/krstock/order/v1/cashBuy" if order.side == OrderSide.BUY else (
            "/krstock/order/v1/cashSell"
        )
        body: dict[str, Any] = {
            "act_no": self._act_no,  # type: ignore[attr-defined]
            "iem_cd": order.symbol,
            "orr_qty": str(order.quantity),
            "orr_pr": str(order.price.amount) if order.price is not None else "0",
            "nmn_pr_tp_cd": _order_division(order.order_type),
            "orr_cnd_dit_cd": "0",
            "ssl_nmn_pr_dit_cd": "0",
            "rmt_mkt_cd": _MARKET_CODE,
            "sor_mkt_sli_yn": "N",
        }
        raw = await self._request("POST", path, body=body)  # type: ignore[attr-defined]
        try:
            output = raw["Output_0"]
            exchange_order_id = str(output["odno"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH 주문 응답에 예상 필드 없음(응답 스키마 미확인 — "
                f"02e 스펙 §3 caveat 참조): {exc}"
            ) from exc
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_order(self, order_id: str) -> bool:
        """**추정 엔드포인트**(모듈 docstring 참조) — 응답 파싱 실패 시
        성공으로 위장하지 않고 그대로 예외를 전파한다(True/False로
        조용히 뭉개지 않음, PM 배정 지침 (2))."""
        body = {
            "act_no": self._act_no,  # type: ignore[attr-defined]
            "orgn_odno": order_id,
            "orr_cnd_dit_cd": "0",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/krstock/order/v1/cashCancel", body=body
        )
        return bool(raw.get("rsp_cd") == "00000")

    @require_paper_sandbox
    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:
        """**추정 엔드포인트**(모듈 docstring 참조)."""
        body: dict[str, Any] = {
            "act_no": self._act_no,  # type: ignore[attr-defined]
            "orgn_odno": order_id,
        }
        if "price" in kwargs:
            body["orr_pr"] = str(kwargs["price"])
        if "quantity" in kwargs:
            body["orr_qty"] = str(kwargs["quantity"])
        await self._request(  # type: ignore[attr-defined]
            "POST", "/krstock/order/v1/cashModify", body=body
        )
        return await self.get_order(order_id)

    async def get_order(self, order_id: str) -> Order:
        """**추정 엔드포인트**(모듈 docstring 참조) — 응답 필드가 예상과
        다르면(스키마 미확인 상태이므로 충분히 가능) 잘못된 Order를
        만드는 대신 즉시 FatalExchangeError로 실패한다."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/krstock/inquiry/v1/orderHistory",
            body={"act_no": self._act_no, "orgn_odno": order_id},  # type: ignore[attr-defined]
        )
        rows = raw.get("Output_0", [])
        if not rows:
            raise FatalExchangeError(f"NH 주문을 찾을 수 없음: order_id={order_id}")
        row = rows[0]
        try:
            filled_qty = Decimal(row.get("ccld_qty", "0"))
            ord_qty = Decimal(row.get("orr_qty", "0"))
        except Exception as exc:  # noqa: BLE001 — 필드 형식 자체가 예상과 다를 수 있음
            raise FatalExchangeError(
                f"NH 주문조회 응답 파싱 실패(응답 스키마 미확인 — 02e 스펙 "
                f"§3 caveat 참조): {exc}"
            ) from exc

        if filled_qty == 0:
            status = OrderStatus.ACKNOWLEDGED
        elif filled_qty < ord_qty:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.FILLED

        return Order(
            order_id=uuid4(),
            exchange_order_id=order_id,
            client_order_id="",
            strategy_id="",
            strategy_version="",
            symbol=row.get("iem_cd", ""),
            exchange="nh",
            side=OrderSide.BUY if row.get("ssl_byv_dit_cd") == "02" else OrderSide.SELL,
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
