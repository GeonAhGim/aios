"""6.7 — BitgetAdapter Trading 메서드군(자금 이동 — 주문 생성/취소/정정).

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§3.2

엔드포인트(2026-08-28 문서 조사 확인 — 실제 응답은 Demo API 키로 라이브
검증 필요, 04번 문서 §11.3 "정직한 최선 추정치" 원칙 그대로 적용):
- POST /api/v2/spot/trade/place-order
- POST /api/v2/spot/trade/cancel-order
- POST /api/v2/spot/trade/cancel-replace-order (FD-4.4 실제 구현)
- POST /api/v2/spot/trade/batch-orders / batch-cancel-order (P1)

2026-09-03 task-1032(PLT-40a 선행) — Plan 주문 메서드군 + health_check()는
`trading_plan_mixin.py`로 분리(P6 line_cap 준수, 순수 이동만, 동작 변경 0).

2026-09-05 task-1519(L4-13) — 조회 메서드군(get_order/get_open_orders/
get_order_history/get_fills + 신규 find_order_by_client_id)과 행 파서
`_row_to_order`는 `trading_query_mixin.py`로 분리. 이 클래스가 그 믹스인을
상속하므로 `BitgetAdapter`의 베이스 목록·MRO상 메서드 해석은 그대로다.
`_row_to_order`는 `ws_parsers.py`가 이 모듈 경로로 import하므로
재수출한다(하위호환).

이 파일의 모든 메서드는 자금을 움직이므로 예외 없이
`@require_paper_sandbox`(task-1045/1356 AST 게이트가 강제)를 갖는다.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.data.models.trading import Order, OrderStatus
from src.exchanges.bitget.account_mode import (
    AccountModeAwareClient,
    BitgetAccountMode,
    RequestSpec,
    account_aware_request,
)
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.bitget.trading_query_mixin import BitgetTradingQueryMixin
from src.exchanges.bitget.trading_query_mixin import _row_to_order as _row_to_order
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from src.exchanges.common.http_client import SignedRequestClient
from src.exchanges.common.live_guard import require_paper_sandbox
from src.services.oms.domain.errors import OrderValidationError
from src.services.oms.domain.rounding import check_notional

__all__ = ["BitgetTradingMixin", "_row_to_order"]

# UTA v3 requires `category` to be specified explicitly (per the official
# upgrade guide, researched 2026-09-09) — this adapter only handles spot
# within Phase 1 scope (doc 06 §6.1), hence a single constant.
_V3_SPOT_CATEGORY = "SPOT"


class _OrderReadingClient(SignedRequestClient, Protocol):
    """modify_order()가 같은 클래스의 get_order()를 호출하지만, self가
    이 파일 안에서 SignedRequestClient로 좁혀진 메서드 안에서는 그 사실이
    보이지 않으므로 명시적으로 계약에 포함한다."""

    async def get_order(self, order_id: str) -> Order: ...


class _AccountModeClient(SignedRequestClient, AccountModeAwareClient, Protocol):
    """L4-31 — place/cancel need `self.account_mode` as well in order to
    assemble the v2/v3 path and body matching the account mode (the common
    SignedRequestClient does not know it)."""


def _reject_if_unsubmittable(order: Order) -> None:
    """FD-4.1 사전검증(task-8073, F4 감사 정정) — BITGET_SPOT_PROFILE의
    tick/lot/min_notional을 거래소 호출 전에 확인한다. 위반 시 거래소를
    부르지 않고 즉시 `OrderValidationError`로 거부한다(fail-closed, §3.4
    `OMS_VALIDATION_{TICK,LOT,MIN_NOTIONAL}`). 심볼이 프로필에 없으면
    (未등록 심볼) 검사를 건너뛴다 — 거래소 응답으로 거부하는 기존 동작을
    그대로 둔다. MARKET 주문(`order.price is None`)은 가격이 없어 tick/
    min_notional 검사 대상이 아니다."""
    tick = BITGET_SPOT_PROFILE.price_tick.get(order.symbol)
    if tick is not None and order.price is not None:
        price = order.price.amount
        if price % tick != 0:
            raise OrderValidationError(
                "TICK",
                f"{order.symbol}: 가격({price})이 tick 단위({tick})에 맞지 않습니다.",
            )

    lot = BITGET_SPOT_PROFILE.qty_lot.get(order.symbol)
    if lot is not None and order.quantity % lot != 0:
        raise OrderValidationError(
            "LOT",
            f"{order.symbol}: 수량({order.quantity})이 lot 단위({lot})에 맞지 않습니다.",
        )

    min_notional = BITGET_SPOT_PROFILE.min_notional.get(order.symbol)
    if min_notional is not None and order.price is not None:
        check_notional(order.price.amount, order.quantity, min_notional)


class BitgetTradingMixin(BitgetTradingQueryMixin):
    @require_paper_sandbox
    async def place_order(self: _AccountModeClient, order: Order) -> Order:
        """L4-31(task-2514) — Classic v2 requires `size`, UTA v3 requires
        `qty` + `category` (per official upgrade guide research,
        **UNVERIFIED** against a real-account round trip). If CLASSIC
        returns 40085, this closure re-invokes in UNIFIED mode and
        reassembles the request with the correct field names (see
        account_mode.account_aware_request — why the retry does not create
        a duplicate order is explained in that docstring)."""
        _reject_if_unsubmittable(order)

        def build(mode: BitgetAccountMode) -> RequestSpec:
            body: dict[str, Any] = {
                "symbol": _to_bitget_symbol(order.symbol),
                "side": order.side.value.lower(),
                "orderType": order.order_type.value.lower(),
                "force": "gtc",
                "clientOid": order.client_order_id,
            }
            if order.price is not None:
                body["price"] = str(order.price.amount)
            if mode is BitgetAccountMode.UNIFIED:
                body["qty"] = str(order.quantity)
                body["category"] = _V3_SPOT_CATEGORY
                path = "/api/v3/trade/place-order"
            else:
                body["size"] = str(order.quantity)
                path = "/api/v2/spot/trade/place-order"
            return "POST", path, None, body

        raw = await account_aware_request(self, build)
        data = raw["data"]
        return order.model_copy(
            update={"exchange_order_id": data["orderId"], "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_order(self: _AccountModeClient, order_id: str) -> bool:
        def build(mode: BitgetAccountMode) -> RequestSpec:
            body: dict[str, Any] = {"orderId": order_id}
            if mode is BitgetAccountMode.UNIFIED:
                body["category"] = _V3_SPOT_CATEGORY
                path = "/api/v3/trade/cancel-order"
            else:
                path = "/api/v2/spot/trade/cancel-order"
            return "POST", path, None, body

        raw = await account_aware_request(self, build)
        return bool(raw.get("code") == "00000")

    @require_paper_sandbox
    async def modify_order(self: _OrderReadingClient, order_id: str, **kwargs: Any) -> Order:
        """02b 스펙 §3.2(FD-4.4 실제 구현) — cancel-replace-order로 지정가
        주문의 가격/수량을 정정한다. 시장가 주문 정정 시도는 FD-4.1(사전
        검증)에서 이미 거래소 호출 전에 차단되므로 여기 도달하는 건 항상
        지정가다."""
        body: dict[str, Any] = {"orderId": order_id}
        if "price" in kwargs:
            body["price"] = str(kwargs["price"])
        if "size" in kwargs:
            body["size"] = str(kwargs["size"])

        raw = await self._request("POST", "/api/v2/spot/trade/cancel-replace-order", body=body)
        data = raw["data"]
        return await self.get_order(data["orderId"])

    @require_paper_sandbox
    async def place_batch_orders(self: SignedRequestClient, orders: list[Order]) -> list[Order]:
        """02b 스펙 §3.2(P1) — FD-19(포트폴리오) 다중 실행 동시 진입용.
        Bitget V2 batch-orders는 한 심볼 안에서만 배치를 허용한다(커뮤니티
        SDK 레퍼런스 기준, 라이브 검증 필요) — 여러 심볼을 섞으면 호출부가
        심볼별로 나눠 호출해야 한다."""
        if not orders:
            return []
        symbol = orders[0].symbol
        order_list: list[dict[str, Any]] = []
        for order in orders:
            row: dict[str, Any] = {
                "side": order.side.value.lower(),
                "orderType": order.order_type.value.lower(),
                "force": "gtc",
                "size": str(order.quantity),
                "clientOid": order.client_order_id,
            }
            if order.price is not None:
                row["price"] = str(order.price.amount)
            order_list.append(row)

        raw = await self._request(
            "POST",
            "/api/v2/spot/trade/batch-orders",
            body={"symbol": _to_bitget_symbol(symbol), "orderList": order_list},
        )
        data = raw["data"]
        success_by_client_oid = {item["clientOid"]: item for item in data.get("successList", [])}
        failed_client_oids = {item["clientOid"] for item in data.get("failureList", [])}

        result = []
        for order in orders:
            if order.client_order_id in success_by_client_oid:
                success = success_by_client_oid[order.client_order_id]
                result.append(
                    order.model_copy(
                        update={
                            "exchange_order_id": success["orderId"],
                            "status": OrderStatus.SUBMITTED,
                        }
                    )
                )
            elif order.client_order_id in failed_client_oids:
                result.append(order.model_copy(update={"status": OrderStatus.REJECTED}))
            else:
                result.append(order)
        return result

    @require_paper_sandbox
    async def cancel_batch_orders(
        self: SignedRequestClient, order_ids: list[str], *, symbol: str | None = None
    ) -> bool:
        """02b 스펙 §3.2(P1)."""
        body: dict[str, Any] = {"orderIdList": [{"orderId": oid} for oid in order_ids]}
        if symbol is not None:
            body["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request("POST", "/api/v2/spot/trade/batch-cancel-order", body=body)
        return bool(raw.get("code") == "00000")
