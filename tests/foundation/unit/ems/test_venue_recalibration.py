"""test_venue_recalibration.py — DoD checklist for venue_recalibration module.

Groups: Positive, Boundary, Negative, Failure injection, Performance, Gate-red.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.ems.domain.route.venue_recalibration import (
    VenueCostSample,
    recalibrate_weights,
)
from src.foundation.ems.domain.route.venue_scoring import VenueScoreWeights

_DEFAULT_CURRENT = VenueScoreWeights(
    fee_weight=Decimal("0.5"),
    liquidity_weight=Decimal("0.5"),
)


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


class TestPositive:
    """Higher cost ratio → higher fee_weight (monotonicity)."""

    def test_monotonic_ratio_increases_fee_weight(self) -> None:
        """Two venues: high-ratio venue present → fee_weight rises."""
        samples = [
            VenueCostSample("A", Decimal("10"), Decimal("1000")),  # ratio 0.01
            VenueCostSample("B", Decimal("50"), Decimal("1000")),  # ratio 0.05
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        # B's ratio is 5× A's → max_ratio/mean > 1 → factor > 1 → delta > 0
        assert result.fee_weight > Decimal("0.5")

    def test_equal_ratios_no_change(self) -> None:
        """Same ratio across venues → calibration factor = 1 → no change."""
        samples = [
            VenueCostSample("A", Decimal("10"), Decimal("1000")),
            VenueCostSample("B", Decimal("20"), Decimal("2000")),
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        # Both ratios = 0.01, mean = max → factor = 1, delta = 0
        assert result.fee_weight == Decimal("0.5")

    def test_single_venue_no_change(self) -> None:
        """Single venue: max = mean → factor = 1."""
        samples = [
            VenueCostSample("X", Decimal("15"), Decimal("1000")),
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        assert result.fee_weight == Decimal("0.5")

    def test_liquidity_weight_preserved(self) -> None:
        """liquidity_weight is unchanged after recalibration."""
        samples = [
            VenueCostSample("A", Decimal("100"), Decimal("1000")),
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        assert result.liquidity_weight == Decimal("0.5")


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


class TestBoundary:
    """Edge cases: single venue, already-weighted, tiny values."""

    def test_tiny_cost_ratio(self) -> None:
        """Very small ratios still produce valid weights."""
        samples = [
            VenueCostSample("A", Decimal("0.001"), Decimal("1000")),
            VenueCostSample("B", Decimal("0.01"), Decimal("1000")),
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        assert result.fee_weight > Decimal("0")
        assert result.liquidity_weight == Decimal("0.5")

    def test_large_current_fee_weight(self) -> None:
        """Large current fee_weight scales proportionally."""
        current = VenueScoreWeights(
            fee_weight=Decimal("5"),
            liquidity_weight=Decimal("1"),
        )
        samples = [
            VenueCostSample("A", Decimal("10"), Decimal("1000")),
            VenueCostSample("B", Decimal("100"), Decimal("1000")),
        ]
        result = recalibrate_weights(samples, current)
        assert result.fee_weight > Decimal("5")

    def test_weight_clamped_to_positive_floor(self) -> None:
        """Even with adverse calibration, weight stays > 0."""
        # A calibration factor < 1 would reduce weight, but 20 % floor
        # keeps it positive.
        current = VenueScoreWeights(
            fee_weight=Decimal("0.001"),
            liquidity_weight=Decimal("0.999"),
        )
        samples = [
            VenueCostSample("A", Decimal("1"), Decimal("1000")),
        ]
        result = recalibrate_weights(samples, current)
        assert result.fee_weight > Decimal("0")


# ---------------------------------------------------------------------------
# Negative tests (≥ 3 required by DoD)
# ---------------------------------------------------------------------------


class TestNegative:
    """Invalid inputs must raise ValueError."""

    def test_empty_samples(self) -> None:
        """Empty sequence → ValueError."""
        with pytest.raises(ValueError, match="at least one sample"):
            recalibrate_weights([], _DEFAULT_CURRENT)

    def test_zero_notional(self) -> None:
        """Zero notional → ValueError."""
        samples = [
            VenueCostSample("A", Decimal("10"), Decimal("0")),
        ]
        with pytest.raises(ValueError, match="notional must be positive"):
            recalibrate_weights(samples, _DEFAULT_CURRENT)

    def test_negative_notional(self) -> None:
        """Negative notional → ValueError."""
        samples = [
            VenueCostSample("A", Decimal("10"), Decimal("-100")),
        ]
        with pytest.raises(ValueError, match="notional must be positive"):
            recalibrate_weights(samples, _DEFAULT_CURRENT)

    def test_zero_total_cost(self) -> None:
        """Zero total_cost → ValueError."""
        samples = [
            VenueCostSample("A", Decimal("0"), Decimal("1000")),
        ]
        with pytest.raises(ValueError, match="total_cost must be positive"):
            recalibrate_weights(samples, _DEFAULT_CURRENT)

    def test_negative_total_cost(self) -> None:
        """Negative total_cost → ValueError."""
        samples = [
            VenueCostSample("A", Decimal("-10"), Decimal("1000")),
        ]
        with pytest.raises(ValueError, match="total_cost must be positive"):
            recalibrate_weights(samples, _DEFAULT_CURRENT)


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Extreme values must not break invariants."""

    def test_extreme_cost_ratio_stays_in_range(self) -> None:
        """Huge cost ratio → fee_weight clamped to +20 %, not Inf."""
        samples = [
            VenueCostSample("A", Decimal("1"), Decimal("1000")),
            VenueCostSample("B", Decimal("999999"), Decimal("1000")),
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        # Max ratio / mean >> 1, so delta clamped to +0.20
        # fee_weight = 0.5 × 1.20 = 0.6
        assert result.fee_weight == Decimal("0.6")
        assert result.fee_weight > Decimal("0")

    def test_many_different_venues(self) -> None:
        """20 venues with varying ratios → no crash, valid output."""
        samples = [
            VenueCostSample(f"V{i}", Decimal(str(i + 1)), Decimal("1000")) for i in range(20)
        ]
        result = recalibrate_weights(samples, _DEFAULT_CURRENT)
        assert result.fee_weight > Decimal("0")
        assert result.liquidity_weight == Decimal("0.5")


# ---------------------------------------------------------------------------
# Performance assertion
# ---------------------------------------------------------------------------


class TestPerformance:
    """1000 samples must complete in reasonable time."""

    @pytest.mark.perf
    def test_1000_samples_under_one_second(self) -> None:
        """Large sample set should finish quickly (< 1 s)."""
        import time

        samples = [
            VenueCostSample(
                f"V{i % 50}",
                Decimal(str((i % 100) + 1)),
                Decimal("1000"),
            )
            for i in range(1000)
        ]
        start = time.perf_counter()
        for _ in range(100):
            recalibrate_weights(samples, _DEFAULT_CURRENT)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"100 recalibrations took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Gate-red proof
# ---------------------------------------------------------------------------


class TestGateRedProof:
    """Mocked samples → verify weight invariants hold."""

    def test_weight_invariant_with_mocked_samples(self) -> None:
        """Even with fabricated data, fee_weight stays in (0, ∞)."""
        # Use samples that would produce an extreme calibration factor.
        samples = [
            VenueCostSample("low", Decimal("0.001"), Decimal("1000")),
            VenueCostSample("high", Decimal("999999"), Decimal("1000")),
        ]
        current = VenueScoreWeights(
            fee_weight=Decimal("0.01"),
            liquidity_weight=Decimal("0.99"),
        )
        result = recalibrate_weights(samples, current)

        # Invariant: fee_weight > 0
        assert result.fee_weight > Decimal("0")
        # Invariant: fee_weight clamped to ≤ current × 1.20
        expected_max = current.fee_weight * Decimal("1.20")
        assert result.fee_weight <= expected_max
        # Invariant: liquidity_weight unchanged
        assert result.liquidity_weight == current.liquidity_weight
