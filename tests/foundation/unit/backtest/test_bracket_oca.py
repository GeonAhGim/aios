"""BT-6 브래킷 청산(OCA) — task-2623, ADR-2026-09-09-B.

`order_types.py`(BT-6)에 있던 나머지 4모듈 테스트(`test_fill_models.py`)와
분리한 이유는 500줄 warn을 넘기지 않기 위해서다(CLAUDE.md §7) — 책임은
여전히 하나(브래킷 청산 OCA 판정 + 부분체결 수량 정합)뿐이다.

패리티(같은 입력 -> PAPER 쪽과 동일 판정)는 이 파일이 아니라
`tests/unit/oms/test_bracket_oca_parity.py`가 두 구현을 나란히 놓고 검증한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.backtest.domain.fill.order_types import (
    OcaResolution,
    bracket_quantity_for_fill,
    resolve_oca,
)

# ---- resolve_oca (bracket exit, N-way OCA) ----


def test_resolve_oca_triggers_profit_leg_only() -> None:
    result = resolve_oca(
        triggered={"profit": True, "loss": False, "trail": False},
        priority_order=("loss", "profit", "trail"),
    )
    assert result == OcaResolution(triggered_leg="profit", cancelled_legs=("loss", "trail"))


def test_resolve_oca_no_leg_triggered() -> None:
    result = resolve_oca(
        triggered={"profit": False, "loss": False, "trail": False},
        priority_order=("loss", "profit", "trail"),
    )
    assert result == OcaResolution(triggered_leg=None, cancelled_legs=())


def test_resolve_oca_same_bar_ambiguity_uses_priority_order() -> None:
    """profit·loss가 같은 봉에서 동시에 닿으면(큰 봉) priority_order가 처음
    나열한 레그가 이긴다 — resolve_oco의 priority_leg와 동일한 원칙."""
    result = resolve_oca(
        triggered={"profit": True, "loss": True, "trail": False},
        priority_order=("loss", "profit", "trail"),
    )
    assert result.triggered_leg == "loss"
    assert set(result.cancelled_legs) == {"profit", "trail"}


def test_resolve_oca_two_leg_bracket_without_trail() -> None:
    result = resolve_oca(
        triggered={"profit": False, "loss": True}, priority_order=("loss", "profit")
    )
    assert result == OcaResolution(triggered_leg="loss", cancelled_legs=("profit",))


def test_resolve_oca_rejects_empty_priority_order() -> None:
    with pytest.raises(ValueError, match="priority_order"):
        resolve_oca(triggered={}, priority_order=())


def test_resolve_oca_rejects_mismatched_leg_sets() -> None:
    with pytest.raises(ValueError, match="triggered"):
        resolve_oca(triggered={"profit": True}, priority_order=("profit", "loss"))


# ---- bracket_quantity_for_fill (부분체결 수량 정합) ----


def test_bracket_quantity_for_fill_clamps_to_partial_entry_fill() -> None:
    """진입이 10 중 4만 체결되면 bracket 청산 레그도 4로 제한된다 — 아직
    채워지지 않은 6주에 대한 청산 의도를 만들지 않는다."""
    result = bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("4"))
    assert result == Decimal("4")


def test_bracket_quantity_for_fill_full_fill_returns_requested_qty() -> None:
    result = bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("10"))
    assert result == Decimal("10")


def test_bracket_quantity_for_fill_overfill_is_still_clamped_to_requested() -> None:
    result = bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("15"))
    assert result == Decimal("10")


def test_bracket_quantity_for_fill_rejects_negative_filled_qty() -> None:
    with pytest.raises(ValueError, match="filled_qty"):
        bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("-1"))


def test_bracket_quantity_for_fill_rejects_nan_requested_qty() -> None:
    with pytest.raises(ValueError, match="requested_qty"):
        bracket_quantity_for_fill(requested_qty=Decimal("NaN"), filled_qty=Decimal("1"))
