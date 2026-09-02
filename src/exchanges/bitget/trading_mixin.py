"""6.7 / 6.8 — BitgetAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§3.2

엔드포인트(2026-08-28 문서 조사 확인, 미체결/이력/체결/정정 4종은
2026-09-02 02b 스펙 작업으로 추가 — 실제 응답은 Demo API 키로 라이브
검증 필요, 04번 문서 §11.3 "정직한 최선 추정치" 원칙 그대로 적용):
- POST /api/v2/spot/trade/place-order
- POST /api/v2/spot/trade/cancel-order
- POST /api/v2/spot/trade/cancel-replace-order (FD-4.4 실제 구현)
- GET  /api/v2/spot/trade/orderInfo
- GET  /api/v2/spot/trade/unfilled-orders (FD-4.5/FD-16.4 보강)
- GET  /api/v2/spot/trade/history-orders (FD-6.4 재시작 정합성 복구 보강)
- GET  /api/v2/spot/trade/fills (평균 체결가 정밀 계산)

02b 스펙 §2 "인터페이스 계약 불변" 원칙 — 아래 신규 메서드들은
`ExchangeAdapter` 추상 인터페이스에 아직 없다(어떤 FD-4/8 호출부도 아직
소비하지 않음, 17.9-A 과잉설계 방지). 실제로 소비하는 호출부가 생기면
그때 ABC로 승격하고 KISAdapter에도 동일 계약을 요구한다 — 지금은
BitgetAdapter 전용 확장 메서드로만 존재한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType

# 확인된 값(라이브/문서 조사) + 미확인 값은 UNKNOWN으로 안전하게 폴백
# (8.3 원칙 — 모르는 상태를 실패로 단정하지 않는다).
_STATUS_MAP = {
    "live": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
}


def _map_status(raw_status: str) -> OrderStatus:
    return _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)


def _row_to_order(data: dict[str, Any]) -> Order:
    """orderInfo/unfilled-orders/history-orders 3개 엔드포인트가 공유하는
    행 형태 — Bitget 스팟 거래 API는 이 3곳에서 동일한 필드 이름을 쓴다
    (place-order/orderInfo에서 이미 확인된 규칙과 동일). `get_order()`의
    기존 파싱 로직을 그대로 뽑아 재사용한다(중복 방지)."""
    status = _map_status(data.get("status", ""))
    filled_quantity = (
        Decimal(data["fillSize"])
        if "fillSize" in data
        else Decimal(data["size"]) if status == OrderStatus.FILLED else Decimal("0")
    )
    return Order(
        order_id=uuid4(),
        exchange_order_id=data["orderId"],
        client_order_id=data.get("clientOid", ""),
        strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
        strategy_version="",
        symbol=data.get("symbol", ""),
        exchange="bitget",
        side=OrderSide(data["side"].upper()) if "side" in data else OrderSide.BUY,
        order_type=(
            OrderType(data["orderType"].upper()) if "orderType" in data else OrderType.LIMIT
        ),
        quantity=Decimal(data.get("size", "0")),
        status=status,
        filled_quantity=filled_quantity,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        asset_class=AssetClass.CRYPTO,
    )


class BitgetTradingMixin:
    async def place_order(self, order: Order) -> Order:
        body: dict[str, Any] = {
            "symbol": order.symbol.replace("/", ""),
            "side": order.side.value.lower(),
            "orderType": order.order_type.value.lower(),
            "force": "gtc",
            "size": str(order.quantity),
            "clientOid": order.client_order_id,
        }
        if order.price is not None:
            body["price"] = str(order.price.amount)

        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/place-order", body=body
        )
        data = raw["data"]
        return order.model_copy(
            update={"exchange_order_id": data["orderId"], "status": OrderStatus.SUBMITTED}
        )

    async def cancel_order(self, order_id: str) -> bool:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/cancel-order", body={"orderId": order_id}
        )
        return bool(raw.get("code") == "00000")

    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:
        """02b 스펙 §3.2(FD-4.4 실제 구현) — cancel-replace-order로 지정가
        주문의 가격/수량을 정정한다. 시장가 주문 정정 시도는 FD-4.1(사전
        검증)에서 이미 거래소 호출 전에 차단되므로 여기 도달하는 건 항상
        지정가다."""
        body: dict[str, Any] = {"orderId": order_id}
        if "price" in kwargs:
            body["price"] = str(kwargs["price"])
        if "size" in kwargs:
            body["size"] = str(kwargs["size"])

        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/cancel-replace-order", body=body
        )
        data = raw["data"]
        return await self.get_order(data["orderId"])

    async def get_order(self, order_id: str) -> Order:
        """편차: 02번 인터페이스가 order_id 하나만으로 완전한 Order를
        반환하도록 요구하지만, Bitget 응답에는 AIOS 전용 컨텍스트(strategy_id/
        strategy_version/asset_class 등)가 없다 — 거래소는 그 개념 자체를
        모른다. 여기서는 거래소가 실제로 아는 필드(상태·체결정보·가격)만
        신뢰할 수 있게 채우고, AIOS 전용 필드는 자리표시자로 둔다 — 호출부
        (Reconciliation, FD-9.6)가 기존 DB 행과 병합해 완성해야 한다."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/orderInfo", params={"orderId": order_id}
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return _row_to_order(data)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """02b 스펙 §3.2 — FD-4.5(UNKNOWN 재조회)/FD-16.4(실행 모니터링)
        보강용. `ExchangeAdapter` ABC에는 아직 없는 Bitget 전용 확장 메서드
        (모듈 docstring 참조)."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/unfilled-orders", params=params or None
        )
        return [_row_to_order(row) for row in raw["data"]]

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]:
        """FD-6.4(재시작 시 정합성 복구) 보강용."""
        params: dict[str, Any] = {"limit": str(limit)}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/history-orders", params=params
        )
        return [_row_to_order(row) for row in raw["data"]]

    async def get_fills(
        self, symbol: str | None = None, *, order_id: str | None = None
    ) -> list[dict[str, Any]]:
        """개별 체결 내역(하나의 주문이 여러 번 나눠 체결될 수 있음) —
        `average_fill_price` 근사치를 정밀 계산하려는 호출부를 위한 원시
        데이터. 별도 모델을 만들지 않고 raw dict를 그대로 반환한다(02b
        §2 모델 재사용 원칙 — 이 데이터를 소비하는 호출부가 생기기 전까지
        구조를 섣불리 확정하지 않는다)."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        if order_id is not None:
            params["orderId"] = order_id
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/fills", params=params or None
        )
        return list(raw["data"])

    async def place_batch_orders(self, orders: list[Order]) -> list[Order]:
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

        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/spot/trade/batch-orders",
            body={"symbol": symbol.replace("/", ""), "orderList": order_list},
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

    async def cancel_batch_orders(self, order_ids: list[str], *, symbol: str | None = None) -> bool:
        """02b 스펙 §3.2(P1)."""
        body: dict[str, Any] = {"orderIdList": [{"orderId": oid} for oid in order_ids]}
        if symbol is not None:
            body["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/batch-cancel-order", body=body
        )
        return bool(raw.get("code") == "00000")

    async def place_plan_order(
        self,
        symbol: str,
        side: OrderSide,
        size: Decimal,
        trigger_price: Decimal,
        *,
        order_price: Decimal | None = None,
        order_type: OrderType = OrderType.LIMIT,
        plan_type: str = "normal_plan",
    ) -> dict[str, Any]:
        """02b 스펙 §3.2(P1) — FD-8.1 stop_loss/take_profit을 폴링 대신
        거래소 네이티브 트리거로 이관할 후보. `Order` 모델에는 트리거가격
        개념이 없다(§2 모델 재사용 원칙 — 실제 소비하는 FD-8 호출부가
        생기기 전까지 새 필드 추가를 보류) — `get_fills()`와 동일하게 raw
        dict를 반환한다."""
        body: dict[str, Any] = {
            "symbol": symbol.replace("/", ""),
            "side": side.value.lower(),
            "orderType": order_type.value.lower(),
            "size": str(size),
            "triggerPrice": str(trigger_price),
            "planType": plan_type,
            "force": "gtc",
        }
        if order_price is not None:
            body["executePrice"] = str(order_price)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/place-plan-order", body=body
        )
        return dict(raw["data"])

    async def cancel_plan_order(self, order_id: str) -> bool:
        """02b 스펙 §3.2(P1)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/cancel-plan-order", body={"orderId": order_id}
        )
        return bool(raw.get("code") == "00000")

    async def get_current_plan_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """02b 스펙 §3.2(P1)."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/current-plan-order", params=params or None
        )
        return list(raw["data"])

    async def health_check(self) -> bool:
        """Watchdog이 State DB와 무관하게 호출하는 경량 응답성 확인."""
        try:
            await self.get_balance()  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
