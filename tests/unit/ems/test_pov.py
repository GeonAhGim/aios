"""EM-10 -- tests/unit/ems/test_pov.py: D2-deepen tests for the POV algorithm.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-10
      docs/design/INVARIANTS.md I-01~I-11

Three tests:
  1. failure_injection — inject a None participation cap into the guard
     and verify ParticipationExceededError (fail-closed).
  2. numerical_assertion — run full_slice_plan and assert that the sum of
     planned_qty across all slices equals the parent order qty (exact
     Decimal arithmetic), and every slice stays within the participation cap.
  3. gate_red_reproduction — construct a scenario where the guard's
     check_participation rejects (planned qty exceeds cap), verify the
     error code and message.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    OrderSide,
    ParentOrder,
    ParentOrderConstraints,
)
from src.foundation.ems.domain.algo.guard import (
    ParticipationExceededError,
    check_participation,
)
from src.foundation.ems.domain.algo.pov import (
    AlgoConstraintError,
    ParentTerminalError,
    _slice_count,
    plan_pov_schedule,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def parent_order() -> ParentOrder:
    """A 10 000-share parent order, 24-hour window, 5 % participation cap."""
    now = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    return ParentOrder(
        parent_id=uuid4(),
        instrument_id="AAPL:NASDAQ",
        side=OrderSide.BUY,
        qty=Decimal("10000"),
        algo=AlgoSpec(
            kind=AlgoKind.POV,
            start=now,
            end=now.replace(hour=16, minute=0),
            max_participation_pct=Decimal("5"),
            slice_interval_sec=60,
            urgency=Decimal("0.5"),
            limit_price=None,
            seed=42,
        ),
        constraints=ParentOrderConstraints(
            max_participation_pct=Decimal("5"),
        ),
        fund_id=uuid4(),
        portfolio_id=uuid4(),
        arrival_ts=now,
    )


@pytest.fixture()
def parent_order_large_qty() -> ParentOrder:
    """A 1 000 000-share parent order for numerical boundary tests."""
    now = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    return ParentOrder(
        parent_id=uuid4(),
        instrument_id="SPY:ARCA",
        side=OrderSide.SELL,
        qty=Decimal("1000000"),
        algo=AlgoSpec(
            kind=AlgoKind.POV,
            start=now,
            end=now.replace(hour=16, minute=0),
            max_participation_pct=Decimal("10"),
            slice_interval_sec=30,
            urgency=Decimal("0.3"),
            limit_price=None,
            seed=99,
        ),
        constraints=ParentOrderConstraints(
            max_participation_pct=Decimal("10"),
        ),
        fund_id=uuid4(),
        portfolio_id=uuid4(),
        arrival_ts=now,
    )


# ---------------------------------------------------------------------------
# Test 1: failure_injection — None participation cap → rejection
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Inject a malformed guard input and verify fail-closed behaviour.

    EM-10 delegates participation checks to the guard module (EM-7).
    When the guard receives None for max_participation_pct, it must reject
    rather than silently allowing unlimited participation.
    """

    def test_none_participation_cap_raises(self) -> None:
        """Guard rejects None cap — the most dangerous failure mode."""
        with pytest.raises(ParticipationExceededError) as exc_info:
            check_participation(
                planned_qty=Decimal("100"),
                market_volume=Decimal("100000"),
                max_participation_pct=None,
            )
        # Fail-closed: the message must explain that None is not treated as
        # unlimited, not just raise a generic ValueError.
        assert "None" in str(exc_info.value)
        assert "never" in str(exc_info.value).lower() or "unlimited" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test 2: numerical_assertion — slice plan sums to parent qty
# ---------------------------------------------------------------------------


class TestNumericalAssertion:
    """Verify that plan_pov_schedule produces exact Decimal arithmetic.

    EM-10 must satisfy I-01 (no float in core domain) and I-05 (every write
    is idempotent/standard). The sum of all slice planned_qty values must
    equal the parent order qty exactly (assuming sufficient market volume).
    """

    def test_slice_sum_equals_parent_qty(self, parent_order: ParentOrder) -> None:
        """10 000 shares → sum of slice planned_qty == 10 000 exactly."""
        # Generate a volume_profile with enough volume per slice so the
        # participation cap is the only limiting factor.
        slice_count = _slice_count(parent_order)
        # 10M per slice is plenty — 5% of 10M = 500k >> 10k total.
        volume_profile = [Decimal("10000000")] * slice_count
        children = plan_pov_schedule(parent_order, volume_profile=volume_profile)
        assert children

        total = sum(c.planned_qty for c in children)
        assert total == parent_order.qty, (
            f"slice sum {total} != parent qty {parent_order.qty}; "
            "float arithmetic leaked into domain logic"
        )

    def test_all_slices_within_participation_cap(self, parent_order: ParentOrder) -> None:
        """Every individual slice must stay within the participation cap."""
        slice_count = _slice_count(parent_order)
        volume_profile = [Decimal("10000000")] * slice_count
        children = plan_pov_schedule(parent_order, volume_profile=volume_profile)
        assert children

        cap = parent_order.algo.max_participation_pct
        for i, child in enumerate(children):
            mv = volume_profile[i]
            participation = (child.planned_qty / mv) * 100
            assert participation <= cap, (
                f"slice {i} participation {participation}% exceeds cap {cap}%"
            )

    def test_no_float_in_slice_quantities(self, parent_order: ParentOrder) -> None:
        """I-01: all planned_qty values must be Decimal, not float."""
        slice_count = _slice_count(parent_order)
        volume_profile = [Decimal("10000000")] * slice_count
        children = plan_pov_schedule(parent_order, volume_profile=volume_profile)
        assert children

        for i, c in enumerate(children):
            assert isinstance(c.planned_qty, Decimal), (
                f"slice {i} planned_qty is {type(c.planned_qty).__name__}, "
                "not Decimal — I-01 violation"
            )

    def test_volume_profile_mismatch_raises(self, parent_order: ParentOrder) -> None:
        """Wrong-length volume_profile → AlgoConstraintError (fail closed)."""
        slice_count = _slice_count(parent_order)
        too_short = [Decimal("1000000")] * (slice_count - 1)
        with pytest.raises(AlgoConstraintError) as exc_info:
            plan_pov_schedule(parent_order, volume_profile=too_short)
        assert str(slice_count) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: gate_red_reproduction — participation cap exceeded
# ---------------------------------------------------------------------------


class TestGateRedReproduction:
    """Reproduce a guard red-light: participation cap exceeded.

    EM-10 must not produce slices that would cause the guard to reject.
    This test constructs a scenario where the guard explicitly rejects
    (participation exceeds cap), verifying the error code and message.
    """

    def test_exceeding_cap_raises_participation_error(self) -> None:
        """Planned qty > cap% of market volume → PARTICIPATION_EXCEEDED."""
        with pytest.raises(ParticipationExceededError) as exc_info:
            check_participation(
                planned_qty=Decimal("6000"),
                market_volume=Decimal("50000"),
                max_participation_pct=Decimal("5"),
            )
        # 6000/50000 = 12%, which exceeds the 5% cap.
        error_msg = str(exc_info.value)
        assert "12" in error_msg or "12.0" in error_msg, (
            f"error message should contain the offending percentage; got: {error_msg}"
        )

    def test_boundary_inclusive_passes(self) -> None:
        """Exact cap — participation == max_participation_pct → no error."""
        # 500/10000 * 100 = 5.0%, cap is 5% → should pass.
        check_participation(
            planned_qty=Decimal("500"),
            market_volume=Decimal("10000"),
            max_participation_pct=Decimal("5"),
        )

    def test_one_unit_over_boundary_fails(self) -> None:
        """Just above the cap boundary → rejection."""
        with pytest.raises(ParticipationExceededError):
            check_participation(
                planned_qty=Decimal("501"),
                market_volume=Decimal("10000"),
                max_participation_pct=Decimal("5"),
            )

    def test_zero_market_volume_rejected(self) -> None:
        """Zero market volume → fail-closed (not ZeroDivisionError)."""
        with pytest.raises(ParticipationExceededError) as exc_info:
            check_participation(
                planned_qty=Decimal("100"),
                market_volume=Decimal("0"),
                max_participation_pct=Decimal("5"),
            )
        assert "0" in str(exc_info.value)

    def test_negative_planned_qty_rejected(self) -> None:
        """Negative planned qty → fail-closed."""
        with pytest.raises(ParticipationExceededError):
            check_participation(
                planned_qty=Decimal("-100"),
                market_volume=Decimal("10000"),
                max_participation_pct=Decimal("5"),
            )

    def test_cap_over_100_rejected(self) -> None:
        """Cap > 100% → fail-closed (nonsensical cap)."""
        with pytest.raises(ParticipationExceededError):
            check_participation(
                planned_qty=Decimal("100"),
                market_volume=Decimal("10000"),
                max_participation_pct=Decimal("150"),
            )


# ---------------------------------------------------------------------------
# Additional EM-10 invariant tests
# ---------------------------------------------------------------------------


class TestPovInvariants:
    """EM-10 specific invariants from I-01~I-11."""

    def test_wrong_algo_kind_raises(self, parent_order: ParentOrder) -> None:
        """Passing a TWAP parent → AlgoConstraintError."""
        from src.foundation.ems.contracts.v1 import AlgoKind

        parent_order.algo.kind = AlgoKind.TWAP
        slice_count = _slice_count(parent_order)
        volume_profile = [Decimal("1000000")] * slice_count
        with pytest.raises(AlgoConstraintError) as exc_info:
            plan_pov_schedule(parent_order, volume_profile=volume_profile)
        assert "TWAP" in str(exc_info.value) or "pov.py" in str(exc_info.value)

    def test_terminal_parent_raises(self, parent_order: ParentOrder) -> None:
        """A terminal parent may not spawn new children (EM-A4)."""
        from src.foundation.ems.contracts.v1 import OrderStatus

        parent_order.status = OrderStatus.FILLED
        slice_count = _slice_count(parent_order)
        volume_profile = [Decimal("1000000")] * slice_count
        with pytest.raises(ParentTerminalError) as exc_info:
            plan_pov_schedule(parent_order, volume_profile=volume_profile)
        assert "terminal" in str(exc_info.value).lower()

    def test_deterministic_children_same_seed(self, parent_order: ParentOrder) -> None:
        """Same parent + same volume_profile → identical children."""
        slice_count = _slice_count(parent_order)
        volume_profile = [Decimal("10000000")] * slice_count
        children_a = plan_pov_schedule(parent_order, volume_profile=volume_profile)
        children_b = plan_pov_schedule(parent_order, volume_profile=volume_profile)
        assert len(children_a) == len(children_b)
        for a, b in zip(children_a, children_b, strict=True):
            assert a.child_id == b.child_id
            assert a.planned_qty == b.planned_qty
            assert a.scheduled_at == b.scheduled_at

    def test_low_volume_leaves_shortfall(self, parent_order: ParentOrder) -> None:
        """When market volume is low, POV legitimately leaves qty unfilled.

        This is by design: POV never exceeds the participation cap, not even
        on the final slice.
        """
        slice_count = _slice_count(parent_order)
        # Very low volume: 100 per slice * 5% = 5 shares per slice.
        # With 234 slices, max fill = 234 * 5 = 1170 << 10000.
        volume_profile = [Decimal("100")] * slice_count
        children = plan_pov_schedule(parent_order, volume_profile=volume_profile)
        total_filled = sum(c.planned_qty for c in children)
        assert total_filled < parent_order.qty, (
            "POV should leave shortfall when volume is insufficient"
        )
        assert total_filled > Decimal("0"), "At least some slices should be planned"
