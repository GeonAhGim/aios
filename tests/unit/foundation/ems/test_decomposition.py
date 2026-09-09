"""EM-13 -- `domain/tca/decomposition.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-13 DoD (a)-(d).
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.domain.tca.benchmarks import Fill
from src.foundation.ems.domain.tca.decomposition import (
    CostDecomposition,
    EmptyFillsError,
    InvalidFillError,
    decompose_cost,
)

_DECOMPOSITION_PATH = (
    Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/tca/decomposition.py"
)

_FILLS = [
    Fill(price=Decimal("100"), qty=Decimal("10")),
    Fill(price=Decimal("110"), qty=Decimal("30")),
]


# -- (b) decomposition sum == total cost, exactly, no rounding error --------


def test_components_sum_to_total_cost_exactly() -> None:
    result = decompose_cost(
        fills=_FILLS,
        side=OrderSide.BUY,
        arrival_price=Decimal("100"),
        vwap_price=Decimal("107.5"),
        close_price=Decimal("109"),
        spread_cost=Decimal("1.23"),
        fees=Decimal("4.56"),
        total_cost=Decimal("500.00"),
    )
    total = (
        result.spread_cost
        + result.market_impact
        + result.delay_cost
        + result.fees
        + result.residual
    )
    assert total == result.total_cost == Decimal("500.00")


def test_residual_absorbs_the_reconciliation_gap_with_zero_rounding_error() -> None:
    # total_cost is deliberately unrelated in scale to the other components,
    # forcing a non-trivial residual -- the invariant must still hold exactly.
    result = decompose_cost(
        fills=_FILLS,
        side=OrderSide.SELL,
        arrival_price=Decimal("100.333333"),
        vwap_price=Decimal("100.666667"),
        close_price=Decimal("101.111111"),
        spread_cost=Decimal("0.01"),
        fees=Decimal("0.02"),
        total_cost=Decimal("12.345678"),
    )
    total = (
        result.spread_cost
        + result.market_impact
        + result.delay_cost
        + result.fees
        + result.residual
    )
    assert total == result.total_cost
    assert isinstance(result.residual, Decimal)


def test_decompose_cost_returns_a_cost_decomposition() -> None:
    result = decompose_cost(
        fills=_FILLS,
        side=OrderSide.BUY,
        arrival_price=Decimal("100"),
        vwap_price=Decimal("107.5"),
        close_price=Decimal("109"),
        spread_cost=Decimal("0"),
        fees=Decimal("0"),
        total_cost=Decimal("300"),
    )
    assert isinstance(result, CostDecomposition)


def test_decomposition_module_never_calls_float() -> None:
    source = _DECOMPOSITION_PATH.read_text(encoding="utf-8")
    assert "float(" not in source, "decomposition.py: float( found -- must stay Decimal-only."


# -- market_impact / delay_cost sign conventions -----------------------------


def test_buy_market_impact_is_positive_when_vwap_is_above_arrival() -> None:
    result = decompose_cost(
        fills=_FILLS,
        side=OrderSide.BUY,
        arrival_price=Decimal("100"),
        vwap_price=Decimal("101"),
        close_price=Decimal("101"),
        spread_cost=Decimal("0"),
        fees=Decimal("0"),
        total_cost=Decimal("40"),
    )
    assert result.market_impact == Decimal("40")  # (101-100) * 40 qty * +1
    assert result.delay_cost == Decimal("0")


def test_sell_market_impact_is_positive_when_vwap_is_below_arrival() -> None:
    result = decompose_cost(
        fills=_FILLS,
        side=OrderSide.SELL,
        arrival_price=Decimal("100"),
        vwap_price=Decimal("99"),
        close_price=Decimal("99"),
        spread_cost=Decimal("0"),
        fees=Decimal("0"),
        total_cost=Decimal("40"),
    )
    assert result.market_impact == Decimal("40")  # (99-100) * 40 qty * -1


# -- (c) reject inputs: empty fills, non-positive qty, non-positive price ---


def test_empty_fills_is_rejected_not_reported_as_zero_cost() -> None:
    with pytest.raises(EmptyFillsError, match="empty"):
        decompose_cost(
            fills=[],
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("100"),
            close_price=Decimal("100"),
            spread_cost=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("0"),
        )


@pytest.mark.parametrize("bad_qty", [Decimal("0"), Decimal("-1")])
def test_non_positive_qty_is_rejected(bad_qty: Decimal) -> None:
    fills = [Fill(price=Decimal("100"), qty=bad_qty)]
    with pytest.raises(InvalidFillError, match="qty"):
        decompose_cost(
            fills=fills,
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("100"),
            close_price=Decimal("100"),
            spread_cost=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("0"),
        )


@pytest.mark.parametrize("bad_price", [Decimal("0"), Decimal("-1")])
def test_non_positive_price_is_rejected(bad_price: Decimal) -> None:
    fills = [Fill(price=bad_price, qty=Decimal("10"))]
    with pytest.raises(InvalidFillError, match="price"):
        decompose_cost(
            fills=fills,
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("100"),
            close_price=Decimal("100"),
            spread_cost=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("0"),
        )


def test_one_bad_fill_among_good_fills_still_rejects_the_whole_batch() -> None:
    fills = [
        Fill(price=Decimal("100"), qty=Decimal("10")),
        Fill(price=Decimal("110"), qty=Decimal("0")),
    ]
    with pytest.raises(InvalidFillError):
        decompose_cost(
            fills=fills,
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("100"),
            close_price=Decimal("100"),
            spread_cost=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("0"),
        )


# -- (d) purely domain: no I/O imports ---------------------------------------


def test_decomposition_module_imports_no_io_or_db() -> None:
    tree = ast.parse(_DECOMPOSITION_PATH.read_text(encoding="utf-8"))
    banned_substrings = ("asyncpg", "sqlalchemy", "adapters", "psycopg")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(b in node.module for b in banned_substrings), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(b in alias.name for b in banned_substrings), alias.name


# -- file size discipline -----------------------------------------------------


def test_decomposition_module_is_at_most_260_lines() -> None:
    line_count = len(_DECOMPOSITION_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 260, f"decomposition.py has {line_count} lines, exceeding the leaf cap."
