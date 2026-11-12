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
    before = recalibrate_weights([_sample("A", "40"), _sample("B", "55")], _DEFAULT_CURRENT)
    after = recalibrate_weights([_sample("A", cost), _sample("B", "55")], _DEFAULT_CURRENT)
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


@pytest.mark.parametrize(("cost", "expected"), [("1", "0.4"), ("50", "0.5"), ("999999", "0.6")])
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


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests — 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


def test_failure_injection_statistics_mean_returns_non_finite_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: statistics.mean이 유한하지 않은 값을 반환하면 ValueError로
    차단된다 — 내부 계산 파이프라인이 사후 검증을 건너뛰지 않음을 확인한다."""
    monkeypatch.setattr(
        "src.foundation.ems.domain.route.venue_recalibration.statistics.mean",
        lambda _: Decimal("Infinity"),
    )
    with pytest.raises(ValueError, match="computed ratio"):
        recalibrate_weights([_sample("A", "50")], _DEFAULT_CURRENT)


def test_failure_injection_corrupted_liquidity_weight_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: persistence 층에서 음수 liquidity_weight가 복원된 경우,
    recalibrate_weights는 fail-closed로 즉시 거부한다 (I-07).
    __post_init__이 생성 단계에서 막으므로 object.__setattr__로 우회 주입."""
    current = VenueScoreWeights(Decimal("0.5"), Decimal("0.5"))
    object.__setattr__(current, "liquidity_weight", Decimal("-0.1"))
    with pytest.raises(ValueError, match="liquidity_weight must be non-negative"):
        recalibrate_weights([_sample("A", "50")], current)


# ──────────────────────────────────────────────────────────────────────
# 3. Gate-red reproduction — 단언이 실제로 잡는지 입력을 망가뜨려 확인
# ──────────────────────────────────────────────────────────────────────


def test_gate_red_weight_clamp_actually_bounds_the_output() -> None:
    """게이트 적색 재현: _MAX_WEIGHT_CHANGE=0.20 클램프가 실제로 동작하는지
    확인한다. 현재 weight 0.5에서 mean(cost/notional)=0.90이면 target은
    0.90이지만 clamp 후 0.70(0.5*1.20)이어야 한다. 단언이 tautology가
    아님을 증명한다."""
    # mean ratio 0.90 — 현재 0.5에서 20% 상한은 0.60
    samples = [VenueCostSample("A", Decimal("90"), Decimal("100"))]
    current = VenueScoreWeights(Decimal("0.5"), Decimal("0.5"))
    result = recalibrate_weights(samples, current)
    # clamp 상한: 0.5 * 1.20 = 0.60
    assert result["A"].fee_weight == Decimal("0.60"), (
        f"Expected clamp to 0.60, got {result['A'].fee_weight}"
    )


def test_gate_red_zero_cost_ratio_rejected() -> None:
    """게이트 적색 재현: total_cost=0이면 ratio=0이고 positive 체크가
    ValueError를 발생시켜야 한다. 단언이 실제로 잡는지 실측한다."""
    with pytest.raises(ValueError, match="total_cost must be positive"):
        recalibrate_weights([VenueCostSample("A", Decimal("0"), Decimal("100"))], _DEFAULT_CURRENT)


def test_gate_red_injected_nan_cost_breaches_finite_check() -> None:
    """게이트 적색 재현: NaN cost를 주입하면 finite 체크가 실제로 ValueError를
    발생시킨다 — 입력 검증이 공허한 단언이 아님을 증명한다."""
    with pytest.raises(ValueError, match="total_cost must be finite"):
        recalibrate_weights(
            [VenueCostSample("A", Decimal("NaN"), Decimal("100"))], _DEFAULT_CURRENT
        )


# ──────────────────────────────────────────────────────────────────────
# 4. Adversarial tests — INVARIANTS 교차 검증
# ──────────────────────────────────────────────────────────────────────


def test_adversarial_multiple_venues_independent_no_cross_contamination() -> None:
    """적대적 테스트: 여러venue의 샘플을 섞어 넣었을 때 각venue의 가중치가
    독립적으로 계산된다 — 한venue의 비용 급증이 다른venue의 가중치를
    왜곡하지 않는다 (I-05: 도메인 로직 공유의 핵심 — 독립성)."""
    samples = [
        _sample("A", "10"),  # low cost
        _sample("B", "90"),  # high cost
        _sample("A", "20"),
        _sample("B", "80"),
        _sample("C", "50"),  # medium cost
    ]
    current = VenueScoreWeights(Decimal("0.5"), Decimal("0.5"))
    result = recalibrate_weights(samples, current)
    # A의 mean ratio ≈ 0.15, B의 mean ratio ≈ 0.85
    # 각venue는 자신의 ratio로만 계산되고, 다른venue의 값으로稀释되지 않음
    assert result["A"].fee_weight < result["C"].fee_weight
    assert result["C"].fee_weight < result["B"].fee_weight
    # liquidity_weight는 모두 동일하게 보존됨
    assert result["A"].liquidity_weight == current.liquidity_weight
    assert result["B"].liquidity_weight == current.liquidity_weight
    assert result["C"].liquidity_weight == current.liquidity_weight


def test_adversarial_rapid_cost_spike_drifts_gradually_not_jumping() -> None:
    """적대적 테스트: 한venue의 비용이 갑자기 10배 올라도 weight는
    ±20%만 이동한다 — single burst of noisy data가 weight를 destabilize
    하지 않는다 (ADR-2026-09-09-B M2-10 drift constraint)."""
    current = VenueScoreWeights(Decimal("0.5"), Decimal("0.5"))
    # 정상 상태: cost 10 → ratio=0.10, clamp [0.4, 0.6] → 0.400
    result_normal = recalibrate_weights([_sample("A", "10")], current)
    assert result_normal["A"].fee_weight == Decimal("0.400")
    # 급격한 비용 상승: cost 100 (10배) → ratio=1.00
    # 현재 weight=0.400이므로 clamp [0.32, 0.48] → 0.48
    result_spike = recalibrate_weights([_sample("A", "100")], result_normal["A"])
    # max change는 ±0.20이므로 0.400 * 1.20 = 0.48을 넘을 수 없음
    assert result_spike["A"].fee_weight == Decimal("0.48"), (
        f"Spike caused {result_spike['A'].fee_weight} != 0.48 clamp limit"
    )
    # 한 번의 호출로 0.5 → 0.48 → 0.576로 뛸 수 없음 (최대 ±0.20)
    result_spike2 = recalibrate_weights([_sample("A", "100")], result_spike["A"])
    assert result_spike2["A"].fee_weight == Decimal("0.576"), (
        f"Second spike caused {result_spike2['A'].fee_weight} != 0.576 clamp limit"
    )
