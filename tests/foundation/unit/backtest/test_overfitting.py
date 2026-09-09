"""Unit tests for `backtest/domain/overfitting.py` -- task-2410 L34 DoD (a)-(f)."""
import math
import random
from decimal import Decimal
from statistics import NormalDist

import pytest

from src.foundation.backtest.domain.overfitting import (
    OVERFITTING_VERSION,
    OverfittingError,
    deflated_sharpe,
    pbo_cscv,
)

_NORMAL = NormalDist()
_GAMMA = 0.5772


def _expected_dsr(
    sr_hat: float, n_trials: int, T: int, skew: float, kurt: float, sr_var: float
) -> float:
    """Independent hand-computation of spec #3.5's DSR formula (statistics.NormalDist
    only) -- does NOT call `deflated_sharpe`, so it is not a tautological check."""
    sr0 = math.sqrt(sr_var) * (
        (1 - _GAMMA) * _NORMAL.inv_cdf(1 - 1 / n_trials)
        + _GAMMA * _NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    )
    numerator = (sr_hat - sr0) * math.sqrt(T - 1)
    denominator = math.sqrt(1 - skew * sr_hat + ((kurt - 1) / 4) * sr_hat**2)
    return _NORMAL.cdf(numerator / denominator)


def test_deflated_sharpe_matches_hand_computed_fixture() -> None:
    expected = _expected_dsr(sr_hat=0.5, n_trials=100, T=250, skew=-0.5, kurt=4.0, sr_var=0.04)
    actual = deflated_sharpe(
        sr_hat=Decimal("0.5"), n_trials=100, T=250,
        skew=Decimal("-0.5"), kurt=Decimal("4.0"), sr_var=Decimal("0.04"),
    )
    assert abs(float(actual) - expected) < 1e-9


def test_pbo_random_matrix_is_near_half() -> None:
    rng = random.Random(20260909)
    matrix = [[Decimal(str(rng.uniform(-1.0, 1.0))) for _ in range(16)] for _ in range(250)]
    first = pbo_cscv(matrix, 8)
    second = pbo_cscv(matrix, 8)
    assert first == second
    assert Decimal("0.35") <= first <= Decimal("0.65")


def _dominant_column_matrix() -> list[list[Decimal]]:
    """Column 0 strictly dominates every other column in every single row, so it is
    always the best-IS AND best-OOS column regardless of the block split -- no
    overfitting signal."""
    rows = []
    for i in range(8):
        rows.append([Decimal(100 + i), Decimal(i), Decimal("50.5") + Decimal(i) / 2, Decimal(-i)])
    return rows


def test_no_overfitting_matrix_has_low_pbo() -> None:
    pbo = pbo_cscv(_dominant_column_matrix(), 4)
    assert pbo <= Decimal("0.2")


def _specialist_column_matrix() -> list[list[Decimal]]:
    """4 blocks of 2 rows each; column c scores 100 in block c's rows and 0 elsewhere.
    Whichever column wins IS necessarily owes its win to a block that is now excluded
    from OOS, so its OOS score is always 0 -- always bottom half."""
    rows = [[Decimal(0)] * 4 for _ in range(8)]
    for block in range(4):
        for row in (2 * block, 2 * block + 1):
            rows[row][block] = Decimal(100)
    return rows


def test_always_overfit_matrix_has_high_pbo() -> None:
    pbo = pbo_cscv(_specialist_column_matrix(), 4)
    assert pbo >= Decimal("0.8")


def test_deflated_sharpe_matches_bailey_lopez_de_prado_2014_numerical_example() -> None:
    """ADR-2026-09-09-B H-9 / spec §10 U3: cross-check against Bailey & Lopez de
    Prado (2014) "The Deflated Sharpe Ratio" §"A Numerical Example". N=100,
    V[SR]=1/2 (annualized), T=1250, skew=-3, kurt=10, SR_hat=2.5 (annualized,
    250 obs/year) -> paper reports SR0~=0.1132 (non-annualized) and
    DSR~=0.9004 < 0.95 (paper's investor rejects the strategy)."""
    sr_hat = Decimal("2.5") / Decimal(250).sqrt()
    sr_var = Decimal("0.5") / Decimal(250)

    dsr = deflated_sharpe(sr_hat, 100, 1250, Decimal(-3), Decimal(10), sr_var)

    assert float(dsr) == pytest.approx(0.9004, abs=1e-4)


def test_deflated_sharpe_matches_bailey_lopez_de_prado_2014_breakeven_trial_counts() -> None:
    """Same paper: had the strategist stopped at N=46 independent trials (same
    non-Normal returns), or had the returns been Normal (skew=0, kurt=3) with
    N=88 trials, the paper reports DSR~=0.9505 both times -- the 95% confidence
    breakeven points either side of the multiple-testing / non-Normality axes."""
    sr_hat = Decimal("2.5") / Decimal(250).sqrt()
    sr_var = Decimal("0.5") / Decimal(250)

    dsr_fewer_trials = deflated_sharpe(sr_hat, 46, 1250, Decimal(-3), Decimal(10), sr_var)
    dsr_normal_returns = deflated_sharpe(sr_hat, 88, 1250, Decimal(0), Decimal(3), sr_var)

    assert float(dsr_fewer_trials) == pytest.approx(0.9505, abs=1e-4)
    assert float(dsr_normal_returns) == pytest.approx(0.9505, abs=1e-4)


def test_pbo_cscv_is_zero_under_pointwise_dominance() -> None:
    """ADR-2026-09-09-B H-9: known-answer synthetic case derived directly from
    Algorithm 2.3 in Bailey, Borwein, Lopez de Prado & Zhu (2015) "The
    Probability of Backtest Overfitting". If one column's performance is
    strictly greater than every other column's in every single row, its
    IS-half mean and OOS-half mean are both the maximum for *every* possible
    CSCV split (the mean preserves pointwise dominance over any subset of
    rows), so it is selected as n* and always ranks best (N) OOS too -> every
    logit lambda_c > 0 -> PBO = 0 exactly, for any valid n_blocks."""
    matrix = [[Decimal(10), Decimal(1), Decimal(0)] for _ in range(8)]

    assert pbo_cscv(matrix, 4) == Decimal(0)


def test_pbo_cscv_is_one_under_zero_sum_reversal_construction() -> None:
    """Known-answer synthetic case derived from Algorithm 2.3: with N=2 columns
    and per-row difference d_i = A_i - B_i chosen so that sum(d_i) == 0 across
    all T=S=4 rows/blocks (here d = [100, 80, -75, -105], no partial pair-sum
    of d is zero), every CSCV split's IS-pair sum of d is the exact negation
    of its complementary OOS-pair sum (the two halves partition the same four
    d_i, which sum to zero). So whichever column wins IS (positive pair-sum)
    necessarily loses OOS (negated, thus negative, pair-sum) in all
    C(4,2)=6 combinations -> PBO = 1 exactly."""
    column_a = [Decimal("100"), Decimal("90"), Decimal("12.5"), Decimal("-2.5")]
    column_b = [Decimal("0"), Decimal("10"), Decimal("87.5"), Decimal("102.5")]
    matrix = [[column_a[i], column_b[i]] for i in range(4)]

    assert pbo_cscv(matrix, 4) == Decimal(1)


def test_deflated_sharpe_rejects_n_trials_below_2() -> None:
    with pytest.raises(OverfittingError):
        deflated_sharpe(
            sr_hat=Decimal("0.5"), n_trials=1, T=250,
            skew=Decimal("0"), kurt=Decimal("3"), sr_var=Decimal("0.04"),
        )


def test_deflated_sharpe_rejects_T_below_2() -> None:
    with pytest.raises(OverfittingError):
        deflated_sharpe(
            sr_hat=Decimal("0.5"), n_trials=100, T=1,
            skew=Decimal("0"), kurt=Decimal("3"), sr_var=Decimal("0.04"),
        )


def test_deflated_sharpe_rejects_non_positive_sr_var() -> None:
    with pytest.raises(OverfittingError):
        deflated_sharpe(
            sr_hat=Decimal("0.5"), n_trials=100, T=250,
            skew=Decimal("0"), kurt=Decimal("3"), sr_var=Decimal("0"),
        )


@pytest.mark.parametrize("n_blocks", [3, 1, 9])
def test_pbo_rejects_invalid_n_blocks(n_blocks: int) -> None:
    matrix = [[Decimal(i + j) for j in range(4)] for i in range(8)]
    with pytest.raises(OverfittingError):
        pbo_cscv(matrix, n_blocks)


def test_pbo_rejects_ragged_matrix() -> None:
    matrix = [[Decimal(1), Decimal(2)], [Decimal(3)]]
    with pytest.raises(OverfittingError):
        pbo_cscv(matrix, 2)


def test_overfitting_version_constant() -> None:
    assert OVERFITTING_VERSION == "ofit-v1"
