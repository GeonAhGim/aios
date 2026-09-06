"""스톱리밋 주문 — 트리거 판정 + 트리거 후 주문 형태 결정(순수)(L4 명세
§9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

스탑은 트리거되면 시장가로 나가 슬리피지에 무방비다. 스탑리밋은 트리거
조건은 스탑과 동일하되(`stop.is_stop_triggered` 재사용), 트리거된 뒤
지정가 주문으로 전환해 체결가에 상한/하한을 건다(대신 미체결 위험을
진다). 트리거가·리밋가 사이 구체 간격(오프셋) 관례는 거래소마다
다르다 — 여기서는 방향(보호적 vs 역방향)만 검증하고 폭은 강제하지
않는다(미검증: 실거래소 문서 대조 필요).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide, OrderType
from src.services.oms.domain.order_types.stop import is_stop_triggered

__all__ = [
    "InvalidStopLimitPriceError",
    "TriggeredOrderSpec",
    "validate_stop_limit_prices",
    "is_stop_limit_triggered",
    "resolve_triggered_order",
]


class InvalidStopLimitPriceError(ValueError):
    """트리거가·리밋가의 방향 관계가 보호적이지 않은 조합 — 트리거되는
    즉시 체결 불가능한 데드 주문이 되므로 제출 시점에 fail-closed로
    거부한다."""


@dataclass(frozen=True, slots=True)
class TriggeredOrderSpec:
    order_type: OrderType
    price: Decimal


def validate_stop_limit_prices(
    *, side: OrderSide, trigger_price: Decimal, limit_price: Decimal
) -> None:
    """매수 스탑리밋은 리밋가가 트리거가 이상(돌파 직후 추격매수를
    허용할 상한), 매도 스탑리밋은 리밋가가 트리거가 이하(손절 직후
    슬리피지를 허용할 하한)여야 방향이 보호적이다."""
    if side == OrderSide.BUY and limit_price < trigger_price:
        raise InvalidStopLimitPriceError(
            f"매수 스탑리밋은 limit_price({limit_price}) >= "
            f"trigger_price({trigger_price})여야 합니다."
        )
    if side == OrderSide.SELL and limit_price > trigger_price:
        raise InvalidStopLimitPriceError(
            f"매도 스탑리밋은 limit_price({limit_price}) <= "
            f"trigger_price({trigger_price})여야 합니다."
        )


def is_stop_limit_triggered(
    *, side: OrderSide, trigger_price: Decimal, last_price: Decimal
) -> bool:
    return is_stop_triggered(side=side, trigger_price=trigger_price, last_price=last_price)


def resolve_triggered_order(*, limit_price: Decimal) -> TriggeredOrderSpec:
    """트리거 후 실제 venue에 전송할 주문 형태 — LIMIT@limit_price."""
    return TriggeredOrderSpec(order_type=OrderType.LIMIT, price=limit_price)
