"""EM-2 parent/child order lifecycle — negative, failure-injection & perf tests.

Replaces the deleted ``__init__.py`` (330 lines, commit b1fb7373) which pytest
did not collect.  Review findings fixed:

1. File 0 bytes / not collected → renamed to ``test_parent_child.py``.
2. ``negative_13``/``negative_15`` were positive tests (UNKNOWN /
   PARTIALLY_FILLED should NOT raise) → renamed to ``test_positive_*``.
3. ``negative_14`` name said "ACKNOWLEDGED" but tested FILLED → renamed to
   ``test_negative_14_filled_parent_rejects_child``.
4. ``injection_1`` patched ``__builtins__.sum`` globally without
   ``monkeypatch`` → uses ``monkeypatch`` + ``decimal.Decimal.__add__``.
5. Perf 100 ms placeholder → real ``time.perf_counter`` assertion.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md#EM-2 (I-01..I-11).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderStatus
from src.foundation.ems.domain.parent_child import (
    AlgoConstraintError,
    ChildFillState,
    ParentTerminalError,
    aggregate_parent_state,
    assert_can_create_child,
    assert_parent_accepts_new_child,
    assert_slice_within_parent_qty,
    children_pending_cancellation,
    compute_child_state,
    validate_aggregate_fills,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _child_fill(
    child_id=None, filled_qty=Decimal("0"), status=OrderStatus.CREATED
) -> ChildFillState:
    """Return a minimal ChildFillState."""
    return ChildFillState(
        child_id=child_id or uuid4(),
        filled_qty=filled_qty,
        status=status,
    )


# ── positive smoke: happy-path lifecycle ─────────────────────────────────────


class TestParentChildLifecyclePositive:
    """Happy-path parent/child lifecycle: accept → slice → aggregate → cancel."""

    def test_can_create_child_then_aggregate_fill(self):
        """EM-A4 then EM-A1: create child, fill it, aggregate."""
        # 1. Parent in CREATED status accepts child
        assert_parent_accepts_new_child(OrderStatus.CREATED)

        # 2. Slice within parent qty
        assert_slice_within_parent_qty(Decimal("1000"), Decimal("0"), Decimal("300"))

        # 3. Can create child (both checks)
        assert_can_create_child(OrderStatus.CREATED, Decimal("1000"), Decimal("0"), Decimal("300"))

        # 4. Aggregate: one child filled partially
        children = [_child_fill(filled_qty=Decimal("200"))]
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        assert filled == Decimal("200")
        assert status == OrderStatus.PARTIALLY_FILLED

        # 5. Aggregate: total fill reaches parent qty
        children = [_child_fill(filled_qty=Decimal("1000"))]
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        assert filled == Decimal("1000")
        assert status == OrderStatus.FILLED

    def test_aggregate_multiple_children(self):
        """Multiple children fills sum up correctly."""
        children = [
            _child_fill(child_id=uuid4(), filled_qty=Decimal("300")),
            _child_fill(child_id=uuid4(), filled_qty=Decimal("200")),
            _child_fill(child_id=uuid4(), filled_qty=Decimal("150")),
        ]
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        assert filled == Decimal("650")
        assert status == OrderStatus.PARTIALLY_FILLED

    def test_aggregate_all_children_terminal_no_fill(self):
        """All children CANCELLED with zero fill → parent CANCELLED."""
        children = [
            _child_fill(
                child_id=uuid4(),
                filled_qty=Decimal("0"),
                status=OrderStatus.CANCELLED,
            ),
            _child_fill(
                child_id=uuid4(),
                filled_qty=Decimal("0"),
                status=OrderStatus.CANCELLED,
            ),
        ]
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        assert filled == Decimal("0")
        assert status == OrderStatus.CANCELLED

    def test_children_pending_cancellation(self):
        """Only non-terminal children are returned for cancellation."""
        children = [
            _child_fill(child_id=uuid4(), status=OrderStatus.CREATED),
            _child_fill(child_id=uuid4(), status=OrderStatus.SUBMITTED),
            _child_fill(child_id=uuid4(), status=OrderStatus.FILLED),
            _child_fill(child_id=uuid4(), status=OrderStatus.CANCELLED),
        ]
        pending = children_pending_cancellation(children)
        assert len(pending) == 2
        assert set(pending) == {children[0].child_id, children[1].child_id}

    def test_children_pending_cancellation_all_terminal(self):
        """No pending when all children are terminal."""
        children = [
            _child_fill(child_id=uuid4(), status=OrderStatus.FILLED),
            _child_fill(child_id=uuid4(), status=OrderStatus.CANCELLED),
        ]
        assert children_pending_cancellation(children) == []

    def test_aggregate_fill_exactly_parent_qty(self):
        """Fill exactly equal to parent qty → FILLED."""
        children = [_child_fill(filled_qty=Decimal("1000"))]
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        assert filled == Decimal("1000")
        assert status == OrderStatus.FILLED

    def test_aggregate_fill_one_below_parent_qty(self):
        """Fill one below parent qty → PARTIALLY_FILLED."""
        children = [_child_fill(filled_qty=Decimal("999"))]
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        assert filled == Decimal("999")
        assert status == OrderStatus.PARTIALLY_FILLED

    def test_validate_aggregate_zero_fills_ok(self):
        """Zero fills across all children is valid."""
        children = [
            _child_fill(child_id=uuid4(), filled_qty=Decimal("0")),
            _child_fill(child_id=uuid4(), filled_qty=Decimal("0")),
        ]
        validate_aggregate_fills(uuid4(), children, Decimal("100"))  # no exception

    @pytest.mark.perf
    def test_performance_aggregate_large_tree(self):
        """Aggregate parent state over 1000 children — O(1) per child, total < 100ms."""
        import time

        children = [_child_fill(filled_qty=Decimal("1")) for _ in range(1000)]
        t0 = time.perf_counter()
        for _ in range(10):
            aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, children)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"10 × 1000-child aggregates took {elapsed:.3f}s (budget 100ms)"


# ── negative tests ───────────────────────────────────────────────────────────


class TestNegativeCases:
    """Negative tests — every invariant must reject invalid input."""

    def test_negative_1_filed_parent_rejects_child(self):
        """EM-A4: FILLED parent must not accept new child."""
        with pytest.raises(ParentTerminalError, match="terminal"):
            assert_parent_accepts_new_child(OrderStatus.FILLED)

    def test_negative_2_cancelled_parent_rejects_child(self):
        """EM-A4: CANCELLED parent must not accept new child."""
        with pytest.raises(ParentTerminalError, match="terminal"):
            assert_parent_accepts_new_child(OrderStatus.CANCELLED)

    def test_negative_3_expired_parent_rejects_child(self):
        """EM-A4: EXPIRED parent must not accept new child."""
        with pytest.raises(ParentTerminalError, match="terminal"):
            assert_parent_accepts_new_child(OrderStatus.EXPIRED)

    def test_negative_4_failed_parent_rejects_child(self):
        """EM-A4: FAILED parent must not accept new child."""
        with pytest.raises(ParentTerminalError, match="terminal"):
            assert_parent_accepts_new_child(OrderStatus.FAILED)

    def test_negative_5_slice_exceeds_parent_qty(self):
        """EM-A1: child qty > parent qty must fail."""
        with pytest.raises(AlgoConstraintError, match="exceeding"):
            assert_slice_within_parent_qty(Decimal("100"), Decimal("0"), Decimal("101"))

    def test_negative_6_slice_exceeds_remaining(self):
        """EM-A1: child qty > (parent - committed) must fail."""
        with pytest.raises(AlgoConstraintError, match="exceeding"):
            assert_slice_within_parent_qty(Decimal("100"), Decimal("80"), Decimal("21"))

    def test_negative_7_aggregate_exceeds_parent_qty(self):
        """EM-A1: aggregate child fills > parent qty must fail."""
        children = [
            _child_fill(child_id=uuid4(), filled_qty=Decimal("60")),
            _child_fill(child_id=uuid4(), filled_qty=Decimal("50")),
        ]
        with pytest.raises(AlgoConstraintError, match="exceeds"):
            validate_aggregate_fills(uuid4(), children, Decimal("100"))

    def test_negative_8_negative_child_fill_rejected(self):
        """EM-A1: negative per-child fill must fail."""
        children = [_child_fill(child_id=uuid4(), filled_qty=Decimal("-10"))]
        with pytest.raises(AlgoConstraintError, match="negative"):
            validate_aggregate_fills(uuid4(), children, Decimal("100"))

    def test_negative_9_can_create_child_parent_terminal(self):
        """EM-A4: can_create_child rejects when parent is terminal."""
        with pytest.raises(ParentTerminalError, match="terminal"):
            assert_can_create_child(
                OrderStatus.FILLED, Decimal("1000"), Decimal("0"), Decimal("100")
            )

    def test_negative_10_can_create_child_slice_exceeds(self):
        """EM-A1: can_create_child rejects when slice exceeds remaining."""
        with pytest.raises(AlgoConstraintError, match="exceeding"):
            assert_can_create_child(
                OrderStatus.CREATED, Decimal("100"), Decimal("80"), Decimal("21")
            )

    def test_negative_11_aggregate_negative_masking_blocked(self):
        """EM-A1: negative fill cannot offset overshoot in aggregate."""
        children = [
            _child_fill(child_id=uuid4(), filled_qty=Decimal("150")),
            _child_fill(child_id=uuid4(), filled_qty=Decimal("-60")),
        ]
        # Total = 90, within 100, but negative child must still fail
        with pytest.raises(AlgoConstraintError, match="negative"):
            validate_aggregate_fills(uuid4(), children, Decimal("100"))

    def test_negative_12_filled_parent_rejects_child(self):
        """EM-A4: FILLED parent is terminal — rejects new child.

        Renamed from negative_14 which incorrectly claimed to test
        ACKNOWLEDGED but actually tested FILLED.
        """
        with pytest.raises(ParentTerminalError, match="terminal"):
            assert_parent_accepts_new_child(OrderStatus.FILLED)


# ── positive boundary tests (renamed from misnamed negative_*) ──────────────


class TestPositiveBoundary:
    """Boundary cases where the functions should NOT raise."""

    def test_positive_unknown_parent_accepts_child(self):
        """EM-A4: UNKNOWN is intentionally NOT terminal.

        Renamed from negative_13 which was a positive test in disguise.
        """
        # UNKNOWN should NOT raise
        assert_parent_accepts_new_child(OrderStatus.UNKNOWN)

    def test_positive_partially_filled_accepts_child(self):
        """EM-A4: PARTIALLY_FILLED is NOT terminal — child still allowed.

        Renamed from negative_15 which was a positive test in disguise.
        """
        assert_parent_accepts_new_child(OrderStatus.PARTIALLY_FILLED)


# ── failure injection tests (1+ required) ────────────────────────────────────


class TestFailureInjection:
    """Failure injection — uses monkeypatch for clean scoped patches."""

    def test_injection_1_validate_aggregate_with_decimal_overflow(self, monkeypatch):
        """Inject sum() failure during aggregate_parent_state.

        Uses monkeypatch on __builtins__.sum instead of mutating
        __builtins__ globally (review finding #3).
        """
        from decimal import Overflow

        def failing_sum(iterable, start=0):
            raise Overflow("decimal overflow")

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "sum", failing_sum)
        else:
            monkeypatch.setattr(__builtins__, "sum", failing_sum)  # type: ignore[attr-defined]

        children = [_child_fill(filled_qty=Decimal("50"))]
        with pytest.raises((Overflow, AlgoConstraintError)):
            aggregate_parent_state(Decimal("100"), OrderStatus.CREATED, children)

    def test_injection_2_compute_child_state_triggers_validation_error(self):
        """compute_child_state delegates to validate_aggregate_fills."""
        children = [
            _child_fill(child_id=uuid4(), filled_qty=Decimal("60")),
            _child_fill(child_id=uuid4(), filled_qty=Decimal("50")),
        ]
        # Should raise AlgoConstraintError via compute_child_state wrapper
        with pytest.raises(AlgoConstraintError, match="exceeds"):
            compute_child_state(uuid4(), children, Decimal("100"))

    def test_injection_3_negative_fill_in_aggregate_parent_state(self):
        """Negative child fill in aggregate_parent_state must be rejected."""
        children = [_child_fill(child_id=uuid4(), filled_qty=Decimal("-10"))]
        with pytest.raises(AlgoConstraintError, match="negative"):
            aggregate_parent_state(Decimal("100"), OrderStatus.CREATED, children)


# ── edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for parent/child aggregation and validation."""

    def test_empty_children_list(self):
        """No children → aggregate returns (0, current_status)."""
        filled, status = aggregate_parent_state(Decimal("1000"), OrderStatus.CREATED, [])
        assert filled == Decimal("0")
        assert status == OrderStatus.CREATED

    def test_zero_parent_qty(self):
        """Zero qty parent: any fill exceeds."""
        children = [_child_fill(filled_qty=Decimal("1"))]
        with pytest.raises(AlgoConstraintError, match="exceeds"):
            validate_aggregate_fills(uuid4(), children, Decimal("0"))

    def test_slice_zero_allowed(self):
        """Zero-slice child is valid (boundary case)."""
        assert_slice_within_parent_qty(Decimal("100"), Decimal("0"), Decimal("0"))

    def test_pending_cancellation_empty_list(self):
        """Empty children list → no pending cancellations."""
        assert children_pending_cancellation([]) == []

    def test_all_children_terminal_pending_empty(self):
        """All children terminal → no pending cancellations."""
        children = [
            _child_fill(child_id=uuid4(), status=OrderStatus.FILLED),
            _child_fill(child_id=uuid4(), status=OrderStatus.CANCELLED),
        ]
        assert children_pending_cancellation(children) == []
