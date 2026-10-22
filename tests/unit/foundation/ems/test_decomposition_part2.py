"""EM-13 -- `domain/tca/decomposition.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-13 DoD (a)-(d).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.domain.tca.benchmarks import Fill
from src.foundation.ems.domain.tca.decomposition import (
    EmptyFillsError,
    decompose_cost,
)

# -- D2 single-call CPU convergence -------------------------------------------

_DECOMPOSITION_PATH = (
    Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/tca/decomposition.py"
)

_FILLS = [
    Fill(price=Decimal("100"), qty=Decimal("10")),
    Fill(price=Decimal("110"), qty=Decimal("30")),
]


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
