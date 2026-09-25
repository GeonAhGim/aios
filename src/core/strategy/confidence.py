"""L10 -- deterministic confidence score from a condition-tree evaluation.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 68, §3.2.

`EvalResult` mirrors the contract §3.2 assigns to `tree_evaluator.py` (L09,
superseded by DSL-8, reassignment forbidden). It is defined here,
its sole consumer, until DSL-8 lands a walker that actually produces one; leaf
counting has no I/O or nondeterminism of its own, so this is a stable contract
to build against ahead of that walker. Leaf-type weighting (crossover 1.0,
comparison 0.5) and the NOT-subtree negation happen upstream, inside whatever
produces `EvalResult` (`crossover_leaves_satisfied`/`satisfied_leaves` already
reflect that); this module only turns the resulting counts into one Decimal.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

_ONE = Decimal("1")
_HALF = Decimal("0.5")
_QUANT = Decimal("0.0001")


class EvalResult(BaseModel):
    matched: bool
    satisfied_leaves: int = Field(ge=0)
    total_leaves: int = Field(ge=0)
    crossover_leaves_satisfied: int = Field(ge=0)
    missing_keys: list[str] = Field(default_factory=list)
    stale_keys: list[str] = Field(default_factory=list)


def compute_confidence(result: EvalResult) -> Decimal:
    """`satisfied/total` leaf ratio, weighted 1.0 for crossover leaves and 0.5
    for comparison leaves (§2 row 68). Always in `[0, 1]` by construction
    (every leaf's weight is <= 1), rounded to 4 decimal places.

    Cross-field consistency (`total_leaves > 0`, `crossover_leaves_satisfied
    <= satisfied_leaves <= total_leaves`) is re-checked here rather than left
    to a pydantic model validator alone: `EvalResult` will eventually be
    produced by an upstream tree walker (DSL-8) this module does not control,
    and `BaseModel.model_construct` can bypass validators entirely -- a
    corrupted upstream result must fail loud here, not silently yield a
    misleading confidence number a trading decision would otherwise consume.
    """
    if result.total_leaves <= 0:
        raise ValueError("EvalResult.total_leaves must be positive")
    if not (0 <= result.satisfied_leaves <= result.total_leaves):
        raise ValueError("EvalResult.satisfied_leaves must be within [0, total_leaves]")
    if not (0 <= result.crossover_leaves_satisfied <= result.satisfied_leaves):
        raise ValueError(
            "EvalResult.crossover_leaves_satisfied must be within [0, satisfied_leaves]"
        )

    comparison_satisfied = result.satisfied_leaves - result.crossover_leaves_satisfied
    weighted = result.crossover_leaves_satisfied * _ONE + comparison_satisfied * _HALF
    confidence = weighted / Decimal(result.total_leaves)
    return confidence.quantize(_QUANT, rounding=ROUND_HALF_UP)


__all__ = ["EvalResult", "compute_confidence"]
