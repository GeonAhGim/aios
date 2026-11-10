"""Additional FA-7 allocation contract tests.

These tests live at the allocation package boundary so the package-level test
target also exercises the domain invariants and dependency failure behavior.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.allocation.domain import policy
from src.foundation.allocation.domain.policy import (
    AllocationResidualError,
    ManualTarget,
    WeightTarget,
    allocate_fixed_weight,
    allocate_manual,
    allocate_pro_rata,
)


def test_pro_rata_rejects_empty_targets_at_package_boundary() -> None:
    """An allocation without recipients must fail closed."""
    with pytest.raises(AllocationResidualError):
        allocate_pro_rata(Decimal("10"), [], Decimal("1"))


def test_fixed_weight_rejects_zero_weight_invariant_violation() -> None:
    """A zero-weight recipient cannot silently receive an allocation."""
    target = WeightTarget(sub_account_id=uuid4(), weight=Decimal("0"))
    with pytest.raises(AllocationResidualError):
        allocate_fixed_weight(Decimal("10"), [target], Decimal("1"))


def test_manual_rejects_negative_quantity_invariant_violation() -> None:
    """Manual quantities must be strictly positive and sum-preserving."""
    target = ManualTarget(sub_account_id=uuid4(), quantity=Decimal("-1"))
    with pytest.raises(AllocationResidualError):
        allocate_manual(Decimal("-1"), [target])


def test_pro_rata_propagates_rounding_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rounding failure must not be converted into a fabricated allocation."""

    def fail_rounding(value: Decimal, quantum: Decimal) -> Decimal:
        raise RuntimeError("rounding dependency unavailable")

    monkeypatch.setattr(policy, "round_to_quantum", fail_rounding)
    target = WeightTarget(sub_account_id=uuid4(), weight=Decimal("1"))
    with pytest.raises(RuntimeError, match="rounding dependency unavailable"):
        allocate_pro_rata(Decimal("10"), [target], Decimal("1"))
