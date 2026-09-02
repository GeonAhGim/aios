"""주문 상태 전이표 + 전이 판정(L4 명세 §4.2).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §4.2, §9 L4-02.

`orders.status`(01번 `OrderStatus`)는 공유접점 §2.3 동결 계약이라 재정의하지
않는다(§3.3) — 이 모듈은 그 값들 사이의 전이 규칙만 순수 함수로 표현한다.

두 공개 API의 역할이 다르다:
- `ALLOWED`: (현재 상태) -> (도달 가능한 상태 집합)의 정적 그래프. 목적지가
  이벤트만으로 정해지지 않는 경우(`RESOLVED_AS(x)`, `RECONCILE_CORRECTION`)에도
  호출부가 "이 목적지가 이 출발점에서 유효한가"를 직접 검증할 수 있다.
- `next_status()`: (현재 상태, 이벤트) -> 목적지가 **결정론적으로 하나뿐인**
  경우만 다루는 편의 함수. 목적지가 데이터(역조회 결과·대사 증거)로 정해지는
  두 이벤트(`RESOLVED_AS`, `RECONCILE_CORRECTION`)와, "주문이 아직 존재하지
  않음"이 출발점인 `SUBMIT_ACCEPTED`는 이 함수의 범위 밖이다(호출하면
  `InvalidOrderTransitionError`) — 그 두 경우는 호출부가 `ALLOWED`로 직접
  목적지 유효성만 확인하고 실제 대입은 스스로 한다.

편차(해석): §4.2 표는 `PARTIALLY_FILLED`발 `FILL`을 명시하지 않지만("SUBMITTED/
ACKNOWLEDGED"만 `from`), 부분체결 주문이 추가로 더 부분체결되는 것은 실거래의
당연한 경로다(30%→60%→100%). `ALLOWED[PARTIALLY_FILLED]`에
`PARTIALLY_FILLED` 자신을 포함시켜 이 경로를 명시적으로 허용한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import Enum

from src.data.models.trading import OrderStatus
from src.services.oms.domain.errors import InvalidOrderTransitionError


class OrderEvent(str, Enum):
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    SENT = "SENT"
    ACK = "ACK"
    VENUE_REJECTED = "VENUE_REJECTED"
    RESPONSE_LOST = "RESPONSE_LOST"
    FILL = "FILL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    VENUE_CANCELLED = "VENUE_CANCELLED"
    VENUE_EXPIRED = "VENUE_EXPIRED"
    MODIFY_REQUESTED = "MODIFY_REQUESTED"
    MODIFIED = "MODIFIED"
    RESOLVED_AS = "RESOLVED_AS"
    RESOLVED_ABSENT = "RESOLVED_ABSENT"
    UNRESOLVED_LIMIT = "UNRESOLVED_LIMIT"
    RECONCILE_CORRECTION = "RECONCILE_CORRECTION"
    # SUBMIT_ACCEPTED는 "주문 생성" 이벤트라 기존 주문의 전이가 아니다(§4.2
    # "from: —") — orders INSERT 시점에 상태를 CREATED로 직접 지정하고,
    # 이 상태기계는 관여하지 않는다. 그래도 order_events 기록을 위해
    # 이벤트 이름 자체는 상수로 남겨둔다.
    SUBMIT_ACCEPTED = "SUBMIT_ACCEPTED"


_TERMINAL_STATES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
        OrderStatus.FAILED,
    }
)

ALLOWED: Mapping[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.VALIDATED, OrderStatus.FAILED}),
    OrderStatus.VALIDATED: frozenset({OrderStatus.SUBMITTED}),
    OrderStatus.SUBMITTED: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.REJECTED,
            OrderStatus.UNKNOWN,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        }
    ),
    OrderStatus.ACKNOWLEDGED: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,  # MODIFIED — 가격/수량 갱신, 상태는 그대로
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,  # 편차(해석) — 위 모듈 docstring 참조
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.UNKNOWN: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
            OrderStatus.UNKNOWN,  # UNRESOLVED_LIMIT — 불변, 상한 초과 시 안전통제로 이어짐
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.FAILED: frozenset(),
}

# next_status()가 이벤트만으로 목적지를 하나로 정할 수 있는 (현재상태, 이벤트)
# 조합. FILL은 filled_qty/qty에 따라 갈리므로 별도 처리(아래 next_status 참조).
_SIMPLE_TRANSITIONS: Mapping[tuple[OrderStatus, OrderEvent], OrderStatus] = {
    (OrderStatus.CREATED, OrderEvent.VALIDATED): OrderStatus.VALIDATED,
    (OrderStatus.CREATED, OrderEvent.VALIDATION_FAILED): OrderStatus.FAILED,
    (OrderStatus.VALIDATED, OrderEvent.SENT): OrderStatus.SUBMITTED,
    (OrderStatus.SUBMITTED, OrderEvent.ACK): OrderStatus.ACKNOWLEDGED,
    (OrderStatus.SUBMITTED, OrderEvent.VENUE_REJECTED): OrderStatus.REJECTED,
    (OrderStatus.SUBMITTED, OrderEvent.RESPONSE_LOST): OrderStatus.UNKNOWN,
    (OrderStatus.ACKNOWLEDGED, OrderEvent.CANCEL_REQUESTED): OrderStatus.ACKNOWLEDGED,
    (OrderStatus.PARTIALLY_FILLED, OrderEvent.CANCEL_REQUESTED): OrderStatus.PARTIALLY_FILLED,
    (OrderStatus.ACKNOWLEDGED, OrderEvent.VENUE_CANCELLED): OrderStatus.CANCELLED,
    (OrderStatus.PARTIALLY_FILLED, OrderEvent.VENUE_CANCELLED): OrderStatus.CANCELLED,
    (OrderStatus.ACKNOWLEDGED, OrderEvent.VENUE_EXPIRED): OrderStatus.EXPIRED,
    (OrderStatus.PARTIALLY_FILLED, OrderEvent.VENUE_EXPIRED): OrderStatus.EXPIRED,
    (OrderStatus.ACKNOWLEDGED, OrderEvent.MODIFY_REQUESTED): OrderStatus.ACKNOWLEDGED,
    (OrderStatus.ACKNOWLEDGED, OrderEvent.MODIFIED): OrderStatus.ACKNOWLEDGED,
    (OrderStatus.UNKNOWN, OrderEvent.RESOLVED_ABSENT): OrderStatus.FAILED,
    (OrderStatus.UNKNOWN, OrderEvent.UNRESOLVED_LIMIT): OrderStatus.UNKNOWN,
}

# next_status()의 지원 범위 밖(목적지가 데이터로 정해짐) — 호출 시 명확한
# 안내 메시지와 함께 거부한다.
_DYNAMIC_TARGET_EVENTS = frozenset({OrderEvent.RESOLVED_AS, OrderEvent.RECONCILE_CORRECTION})


def is_terminal(status: OrderStatus) -> bool:
    return status in _TERMINAL_STATES


def next_status(
    current: OrderStatus,
    event: OrderEvent,
    *,
    filled_qty: Decimal | None = None,
    qty: Decimal | None = None,
) -> OrderStatus:
    if is_terminal(current):
        raise InvalidOrderTransitionError(
            f"{current.value}는 터미널 상태입니다 — 어떤 이벤트도 받을 수 없습니다"
            f"(event={event.value})."
        )

    if event is OrderEvent.SUBMIT_ACCEPTED:
        raise InvalidOrderTransitionError(
            "SUBMIT_ACCEPTED는 신규 주문 생성 이벤트라 기존 상태의 전이가 아닙니다 "
            "— orders INSERT 시 status=CREATED를 직접 지정하세요."
        )
    if event in _DYNAMIC_TARGET_EVENTS:
        raise InvalidOrderTransitionError(
            f"{event.value}은 목적지가 데이터(역조회 결과·대사 증거)로 정해집니다 — "
            "next_status()가 아니라 ALLOWED[current]로 목적지 유효성만 확인하고 "
            "호출부가 직접 대입하세요."
        )

    if event is OrderEvent.FILL:
        if current not in (
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            raise InvalidOrderTransitionError(f"{current.value}에서는 FILL을 받을 수 없습니다.")
        if filled_qty is None or qty is None:
            raise InvalidOrderTransitionError("FILL 이벤트는 filled_qty/qty가 필수입니다.")
        if filled_qty <= 0:
            raise InvalidOrderTransitionError("filled_qty가 0 이하인 FILL은 유효하지 않습니다.")
        return OrderStatus.FILLED if filled_qty >= qty else OrderStatus.PARTIALLY_FILLED

    target = _SIMPLE_TRANSITIONS.get((current, event))
    if target is None:
        raise InvalidOrderTransitionError(
            f"{current.value} -({event.value})-> ? 는 전이표에 없는 조합입니다."
        )
    return target
