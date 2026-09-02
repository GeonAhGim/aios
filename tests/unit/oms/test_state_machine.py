"""주문 상태 전이표 단위테스트 — L4-02. DB 없음."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.state_machine import (
    ALLOWED,
    OrderEvent,
    is_terminal,
    next_status,
)

_TERMINAL = (
    OrderStatus.FILLED,
    OrderStatus.REJECTED,
    OrderStatus.CANCELLED,
    OrderStatus.EXPIRED,
    OrderStatus.FAILED,
)


@pytest.mark.parametrize("status", _TERMINAL)
def test_terminal_states_are_terminal(status: OrderStatus) -> None:
    assert is_terminal(status) is True
    assert ALLOWED[status] == frozenset()


def test_unknown_is_not_terminal() -> None:
    assert is_terminal(OrderStatus.UNKNOWN) is False


@pytest.mark.parametrize("status", _TERMINAL)
def test_terminal_state_rejects_any_event(status: OrderStatus) -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(status, OrderEvent.ACK)


def test_created_validated_to_validated() -> None:
    assert next_status(OrderStatus.CREATED, OrderEvent.VALIDATED) == OrderStatus.VALIDATED


def test_created_validation_failed_to_failed() -> None:
    assert next_status(OrderStatus.CREATED, OrderEvent.VALIDATION_FAILED) == OrderStatus.FAILED


def test_validated_sent_to_submitted() -> None:
    assert next_status(OrderStatus.VALIDATED, OrderEvent.SENT) == OrderStatus.SUBMITTED


def test_submitted_ack_to_acknowledged() -> None:
    assert next_status(OrderStatus.SUBMITTED, OrderEvent.ACK) == OrderStatus.ACKNOWLEDGED


def test_submitted_venue_rejected_to_rejected() -> None:
    assert next_status(OrderStatus.SUBMITTED, OrderEvent.VENUE_REJECTED) == OrderStatus.REJECTED


def test_submitted_response_lost_to_unknown() -> None:
    assert next_status(OrderStatus.SUBMITTED, OrderEvent.RESPONSE_LOST) == OrderStatus.UNKNOWN


@pytest.mark.parametrize(
    "current", (OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED)
)
def test_fill_partial_when_less_than_quantity(current: OrderStatus) -> None:
    result = next_status(current, OrderEvent.FILL, filled_qty=Decimal("0.3"), qty=Decimal("1"))
    assert result == OrderStatus.PARTIALLY_FILLED


@pytest.mark.parametrize(
    "current", (OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED)
)
def test_fill_complete_when_equal_to_quantity(current: OrderStatus) -> None:
    result = next_status(current, OrderEvent.FILL, filled_qty=Decimal("1"), qty=Decimal("1"))
    assert result == OrderStatus.FILLED


def test_fill_over_quantity_still_counts_as_filled() -> None:
    """lot 오차 허용(§4.2 "lot 오차 ≤ 1 lot") — 초과분은 여기서 판단하지
    않고 FILLED로 확정만 한다(오차 검증은 이 함수 밖 책임)."""
    result = next_status(
        OrderStatus.PARTIALLY_FILLED, OrderEvent.FILL, filled_qty=Decimal("1.001"), qty=Decimal("1")
    )
    assert result == OrderStatus.FILLED


def test_fill_zero_quantity_is_invalid() -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(
            OrderStatus.SUBMITTED, OrderEvent.FILL, filled_qty=Decimal("0"), qty=Decimal("1")
        )


def test_fill_missing_quantities_is_invalid() -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(OrderStatus.SUBMITTED, OrderEvent.FILL)


def test_fill_from_validated_is_invalid() -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(
            OrderStatus.VALIDATED, OrderEvent.FILL, filled_qty=Decimal("1"), qty=Decimal("1")
        )


@pytest.mark.parametrize("current", (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED))
def test_cancel_requested_is_unchanged(current: OrderStatus) -> None:
    assert next_status(current, OrderEvent.CANCEL_REQUESTED) == current


@pytest.mark.parametrize("current", (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED))
def test_venue_cancelled_to_cancelled(current: OrderStatus) -> None:
    assert next_status(current, OrderEvent.VENUE_CANCELLED) == OrderStatus.CANCELLED


@pytest.mark.parametrize("current", (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED))
def test_venue_expired_to_expired(current: OrderStatus) -> None:
    assert next_status(current, OrderEvent.VENUE_EXPIRED) == OrderStatus.EXPIRED


def test_modify_requested_and_modified_keep_acknowledged() -> None:
    assert (
        next_status(OrderStatus.ACKNOWLEDGED, OrderEvent.MODIFY_REQUESTED)
        == OrderStatus.ACKNOWLEDGED
    )
    assert next_status(OrderStatus.ACKNOWLEDGED, OrderEvent.MODIFIED) == OrderStatus.ACKNOWLEDGED


def test_unknown_resolved_absent_to_failed() -> None:
    assert next_status(OrderStatus.UNKNOWN, OrderEvent.RESOLVED_ABSENT) == OrderStatus.FAILED


def test_unknown_unresolved_limit_is_unchanged() -> None:
    assert next_status(OrderStatus.UNKNOWN, OrderEvent.UNRESOLVED_LIMIT) == OrderStatus.UNKNOWN


def test_partially_filled_can_receive_another_partial_fill() -> None:
    """편차(해석) — state_machine.py 모듈 docstring 참조."""
    assert OrderStatus.PARTIALLY_FILLED in ALLOWED[OrderStatus.PARTIALLY_FILLED]


@pytest.mark.parametrize(
    "event", (OrderEvent.RESOLVED_AS, OrderEvent.RECONCILE_CORRECTION)
)
def test_dynamic_target_events_are_rejected_by_next_status(event: OrderEvent) -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(OrderStatus.UNKNOWN, event)


def test_submit_accepted_is_rejected_by_next_status() -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(OrderStatus.CREATED, OrderEvent.SUBMIT_ACCEPTED)


def test_out_of_table_combination_raises() -> None:
    with pytest.raises(InvalidOrderTransitionError):
        next_status(OrderStatus.CREATED, OrderEvent.ACK)


def test_allowed_covers_every_order_status() -> None:
    """전이표 밖 상태가 없어야 한다 — OrderStatus의 모든 값이 ALLOWED에
    키로 존재해야 fail-closed가 성립한다."""
    for status in OrderStatus:
        assert status in ALLOWED, f"{status}가 ALLOWED에 없음"
