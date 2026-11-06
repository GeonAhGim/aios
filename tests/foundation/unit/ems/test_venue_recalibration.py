"""M2-10: venue-local calibration, validation, bounds and CPU budget."""

from decimal import Decimal

import pytest

from src.foundation.ems.domain.route.venue_recalibration import (
    VenueCostSample,
    recalibrate_weights,
)
from src.foundation.ems.domain.route.venue_scoring import VenueScoreWeights
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_DEFAULT_CURRENT = VenueScoreWeights(Decimal("0.5"), Decimal("0.5"))


def _sample(venue: str, cost: str, notional: str = "100") -> VenueCostSample:
    return VenueCostSample(venue, Decimal(cost), Decimal(notional))


@pytest.mark.parametrize("cost", ["45", "50", "55", "100", "999999"])
def test_venue_cost_increase_never_reduces_weight(cost: str) -> None:
    # Old max/mean fell from 0.578947... to 0.5 when A rose from 40 to 55.
    before = recalibrate_weights(
        [_sample("A", "40"), _sample("B", "55")], _DEFAULT_CURRENT
    )
    after = recalibrate_weights(
        [_sample("A", cost), _sample("B", "55")], _DEFAULT_CURRENT
    )
    assert after["A"].fee_weight > before["A"].fee_weight
    assert after["B"] == before["B"]
    assert Decimal("0.4") <= after["A"].fee_weight <= Decimal("0.6")


def test_venue_means_are_independent_of_other_venues_and_sample_counts() -> None:
    samples = [_sample("A", "40"), _sample("A", "120", "200"), _sample("B", "55")]
    result = recalibrate_weights(samples, _DEFAULT_CURRENT)
    assert result["A"].fee_weight == Decimal("0.5")
    assert result["B"].fee_weight == Decimal("0.55")
    assert result == recalibrate_weights(list(reversed(samples)), _DEFAULT_CURRENT)
    assert result == recalibrate_weights(samples + [_sample("B", "55")] * 10, _DEFAULT_CURRENT)
    assert all(w.liquidity_weight == _DEFAULT_CURRENT.liquidity_weight for w in result.values())
    assert _DEFAULT_CURRENT.fee_weight == Decimal("0.5")


@pytest.mark.parametrize(
    ("cost", "expected"), [("1", "0.4"), ("50", "0.5"), ("999999", "0.6")]
)
def test_single_venue_target_and_both_change_caps(cost: str, expected: str) -> None:
    result = recalibrate_weights([_sample("A", cost)], _DEFAULT_CURRENT)
    assert result["A"].fee_weight == Decimal(expected)


@pytest.mark.parametrize("current_fee", ["0", "0.00001", "0.0001", "0.00012"])
def test_actual_positive_floor(current_fee: str) -> None:
    current = VenueScoreWeights(Decimal(current_fee), Decimal("0.999"))
    result = recalibrate_weights([_sample("A", "0.000001")], current)
    assert result["A"].fee_weight == Decimal("0.0001")
    assert result["A"].liquidity_weight == current.liquidity_weight


def test_empty_samples_rejected() -> None:
    with pytest.raises(ValueError, match="at least one sample"):
        recalibrate_weights([], _DEFAULT_CURRENT)


@pytest.mark.parametrize("field", ["total_cost", "notional"])
@pytest.mark.parametrize("value", ["0", "-1", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_invalid_samples_rejected_before_comparison(field: str, value: str) -> None:
    cost = Decimal(value) if field == "total_cost" else Decimal("1")
    notional = Decimal(value) if field == "notional" else Decimal("100")
    reason = "positive" if value in ("0", "-1") else "finite"
    with pytest.raises(ValueError, match=f"{field} must be {reason}"):
        recalibrate_weights([VenueCostSample("A", cost, notional)], _DEFAULT_CURRENT)


@pytest.mark.parametrize("field", ["fee_weight", "liquidity_weight"])
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "-1"])
def test_corrupted_current_weights_fail_closed(field: str, value: str) -> None:
    current = VenueScoreWeights(Decimal("0.5"), Decimal("0.5"))
    # Inject invalid persisted state without the DTO's ordering validation.
    object.__setattr__(current, field, Decimal(value))
    with pytest.raises(ValueError, match=field):
        recalibrate_weights([_sample("A", "50")], current)


def test_invalid_computed_mean_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.foundation.ems.domain.route.venue_recalibration.statistics.mean",
        lambda _: Decimal("sNaN"),
    )
    with pytest.raises(ValueError, match="computed ratio"):
        recalibrate_weights([_sample("A", "50")], _DEFAULT_CURRENT)


def _large_samples() -> list[VenueCostSample]:
    return [_sample(f"V{i % 50}", str((i % 100) + 1)) for i in range(1000)]


@pytest.mark.perf
def test_1000_samples_cpu_budget(perf_budget: PerfBudget) -> None:
    samples = _large_samples()
    measurement = perf_budget.best_of(
        lambda: recalibrate_weights(samples, _DEFAULT_CURRENT), n=5, batch=100
    )
    assert measurement.cpu_ms < 10.0, f"cpu_ms={measurement.cpu_ms:.3f}"


def test_same_perf_budget_rejects_injected_delay(perf_budget: PerfBudget) -> None:
    samples = _large_samples()
    delayed = virtual_delayed_sync(
        lambda: recalibrate_weights(samples, _DEFAULT_CURRENT), seconds=0.03
    )
    measurement = perf_budget.best_of(delayed, n=5, batch=1)
    with pytest.raises(AssertionError, match="cpu_ms="):
        assert measurement.cpu_ms < 10.0, f"cpu_ms={measurement.cpu_ms:.3f}"
