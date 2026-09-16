"""task-2623 DoD — 백테스트(BT-6)·PAPER(EM-19) 브래킷 청산 패리티.

Spec: ADR-2026-09-09-B. `src/foundation/backtest/domain/fill/order_types.py`
(BT-6)와 `src/services/oms/domain/order_types/oca.py`(EM-19)는 서로
임포트하지 않는 독립된 두 구현이다(bounded-context 분리, `resolve_oco`가
이미 양쪽에 중복 구현된 것과 같은 원칙) — 이 파일이 "같은 입력 -> 같은
체결 로그"라는 패리티 계약을 실제로 증명한다: 동일한 트리거 시나리오
행렬을 두 구현에 동시에 먹여서 `resolve_oca`의 트리거 레그·취소 레그
집합과 `bracket_quantity_for_fill`의 부분체결 정합 수량이 바이트가 아니라
값 단위로 완전히 같은지 단언한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.backtest.domain.fill import order_types as bt6
from src.services.oms.domain.order_types import oca as em19

_PRIORITY_ORDER = ("loss", "profit", "trail")

# (profit_triggered, loss_triggered, trail_triggered) -> 두 구현 모두 같은
# priority_order로 판정했을 때 기대하는 (triggered_leg, cancelled_legs 집합).
_SCENARIOS: tuple[tuple[bool, bool, bool], ...] = (
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, False),  # 같은 틱 동시 트리거 -> priority_order(loss 우선)
    (True, False, True),
    (False, True, True),
    (True, True, True),
    (False, False, False),  # 아무 레그도 안 닿음
)


@pytest.mark.parametrize(("profit", "loss", "trail"), _SCENARIOS)
def test_resolve_oca_is_byte_for_byte_identical_across_backtest_and_paper(
    profit: bool, loss: bool, trail: bool
) -> None:
    triggered = {"profit": profit, "loss": loss, "trail": trail}
    backtest_result = bt6.resolve_oca(triggered=triggered, priority_order=_PRIORITY_ORDER)
    paper_result = em19.resolve_oca(triggered=triggered, priority_order=_PRIORITY_ORDER)
    assert backtest_result.triggered_leg == paper_result.triggered_leg
    assert backtest_result.cancelled_legs == paper_result.cancelled_legs


@pytest.mark.parametrize(
    ("requested_qty", "filled_qty"),
    [
        (Decimal("10"), Decimal("10")),  # 완전체결
        (Decimal("10"), Decimal("4")),  # 부분체결 -- bracket 수량은 4로 클램프
        (Decimal("10"), Decimal("0")),  # 미체결 -- bracket 수량 0
        (Decimal("7.5"), Decimal("15")),  # 방어적 상한 -- 요청 수량을 넘지 않음
    ],
)
def test_bracket_quantity_for_fill_is_identical_across_backtest_and_paper(
    requested_qty: Decimal, filled_qty: Decimal
) -> None:
    backtest_qty = bt6.bracket_quantity_for_fill(requested_qty=requested_qty, filled_qty=filled_qty)
    paper_qty = em19.bracket_quantity_for_fill(requested_qty=requested_qty, filled_qty=filled_qty)
    assert backtest_qty == paper_qty


def test_partial_entry_fill_produces_matching_bracket_exit_log_on_both_sides() -> None:
    """DoD 시나리오 그대로: 같은 스크립트(bracket qty=10, profit/loss 설정)가
    같은 데이터(진입이 4만 체결)를 만났을 때, 두 체결 모델이 만들어내는
    "브래킷 청산 로그"(레그별 청산 수량)가 완전히 같아야 한다."""
    requested_qty = Decimal("10")
    filled_qty = Decimal("4")
    triggered = {"profit": False, "loss": True, "trail": False}

    backtest_qty = bt6.bracket_quantity_for_fill(requested_qty=requested_qty, filled_qty=filled_qty)
    backtest_resolution = bt6.resolve_oca(triggered=triggered, priority_order=_PRIORITY_ORDER)

    paper_qty = em19.bracket_quantity_for_fill(requested_qty=requested_qty, filled_qty=filled_qty)
    paper_resolution = em19.resolve_oca(triggered=triggered, priority_order=_PRIORITY_ORDER)

    assert backtest_qty == paper_qty == Decimal("4")
    assert backtest_resolution.triggered_leg == paper_resolution.triggered_leg == "loss"
    assert (
        backtest_resolution.cancelled_legs == paper_resolution.cancelled_legs == ("profit", "trail")
    )
