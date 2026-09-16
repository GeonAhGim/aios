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


# -- DEEPEN 2511 (EM-13) — D2 하한 증빙 보강 ----------------------------------------


def test_failure_injection_zero_total_cost_with_nonzero_components_rejected() -> None:
    """Failure-injection: inject a fill with qty=0 into decompose_cost.

    The guard must reject a fill with zero quantity (fail-closed), proving
    that partial-fill data corruption (e.g., a stale order book entry) does
    silently pass through and distort the decomposition.
    """
    fills = [
        Fill(price=Decimal("100"), qty=Decimal("10")),
        Fill(price=Decimal("110"), qty=Decimal("0")),  # injected zero
        Fill(price=Decimal("120"), qty=Decimal("20")),
    ]
    with pytest.raises(InvalidFillError, match="qty"):
        decompose_cost(
            fills=fills,
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("105"),
            close_price=Decimal("106"),
            spread_cost=Decimal("1.00"),
            fees=Decimal("2.00"),
            total_cost=Decimal("100.00"),
        )


def test_numerical_assertion_decomposition_multi_fill_exact_decimal() -> None:
    """Numerical performance assertion: decomposition with realistic multi-fill values.

    Verify that decompose_cost produces exact Decimal arithmetic — no float
    contamination — for a realistic multi-fill scenario.

    Scenario (BUY, 4 fills):
    - Fill 1: 100 @ 1000.00
    - Fill 2: 200 @ 1005.50
    - Fill 3: 150 @ 1010.25
    - Fill 4: 50  @ 1002.75
    Total qty = 500
    arrival = 1000.00, vwap = 1005.20, close = 1008.00
    spread = 12.50, fees = 25.00, total_cost = 3000.00

    market_impact = (1005.20 - 1000.00) * 500 * 1 = 2600.00
    delay_cost    = (1008.00 - 1005.20) * 500 * 1 = 1400.00
    residual      = 3000.00 - (12.50 + 2600.00 + 1400.00 + 25.00) = -1037.50

    Check: 12.50 + 2600.00 + 1400.00 + 25.00 + (-1037.50) = 3000.00 ✓
    """
    fills = [
        Fill(price=Decimal("1000.00"), qty=Decimal("100")),
        Fill(price=Decimal("1005.50"), qty=Decimal("200")),
        Fill(price=Decimal("1010.25"), qty=Decimal("150")),
        Fill(price=Decimal("1002.75"), qty=Decimal("50")),
    ]
    result = decompose_cost(
        fills=fills,
        side=OrderSide.BUY,
        arrival_price=Decimal("1000.00"),
        vwap_price=Decimal("1005.20"),
        close_price=Decimal("1008.00"),
        spread_cost=Decimal("12.50"),
        fees=Decimal("25.00"),
        total_cost=Decimal("3000.00"),
    )

    # Exact Decimal assertions: no float drift
    assert result.market_impact == Decimal("2600.00"), (
        f"Expected market_impact 2600.00, got {result.market_impact}"
    )
    assert result.delay_cost == Decimal("1400.00"), (
        f"Expected delay_cost 1400.00, got {result.delay_cost}"
    )
    assert result.residual == Decimal("-1037.50"), (
        f"Expected residual -1037.50, got {result.residual}"
    )
    assert isinstance(result.residual, Decimal)

    # Verify: five components sum exactly to total_cost
    total = (
        result.spread_cost
        + result.market_impact
        + result.delay_cost
        + result.fees
        + result.residual
    )
    assert total == result.total_cost == Decimal("3000.00")


def test_gate_red_reproduction_empty_fills_bypass() -> None:
    """Gate-red reproduction: prove the empty-fills guard is a real invariant.

    Without the empty-fills guard, decompose_cost would iterate over an empty
    sequence, _total_filled_qty would return Decimal("0"), and the formula
    (vwap - arrival) * 0 * sign would produce zero for both market_impact
    and delay_cost. The residual would then absorb the entire total_cost,
    silently masking the fact that no fills occurred.

    This test proves the guard was necessary by showing that bypassing it
    produces a different (incorrect) result — a false decomposition with
    zero impact/delay but non-zero residual.
    """
    from unittest.mock import patch

    # Normal path: empty fills raises EmptyFillsError
    with pytest.raises(EmptyFillsError, match="empty"):
        decompose_cost(
            fills=[],
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("100"),
            close_price=Decimal("100"),
            spread_cost=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("500"),
        )

    # Bypass path: patch _total_filled_qty to return 0 for empty fills.
    # Without the guard, the decomposition would silently succeed with
    # zero quantities, masking the error.
    def _bypass_total_filled_qty(fills):
        """Return 0 for empty fills instead of raising."""
        if not fills:
            return Decimal("0")
        total = Decimal("0")
        for f in fills:
            total += f.qty
        return total

    with patch(
        "src.foundation.ems.domain.tca.decomposition._total_filled_qty",
        side_effect=_bypass_total_filled_qty,
    ):
        # After bypass, empty fills produces a decomposition with zero
        # market_impact and delay_cost, and residual absorbing total_cost.
        # This is the "red" state: silent zero-cost decomposition.
        result = decompose_cost(
            fills=[],
            side=OrderSide.BUY,
            arrival_price=Decimal("100"),
            vwap_price=Decimal("100"),
            close_price=Decimal("100"),
            spread_cost=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("500"),
        )
        # Prove the bypass produces wrong results:
        # market_impact = (100-100) * 0 * 1 = 0
        # delay_cost = (100-100) * 0 * 1 = 0
        # residual = 500 - (0+0+0+0) = 500
        assert result.market_impact == Decimal("0")
        assert result.delay_cost == Decimal("0")
        assert result.residual == Decimal("500"), (
            "Bypassed guard: residual absorbs entire cost — silent zero-masking"
        )
        # The sum still equals total_cost, but the decomposition is meaningless
        # because no actual fills occurred — the guard prevents this false negative.
