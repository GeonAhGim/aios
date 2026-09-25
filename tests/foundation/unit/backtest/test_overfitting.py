"""Unit tests for `backtest/domain/overfitting.py` -- task-2410 L34 DoD (a)-(f)."""

import decimal
import math
import random
import time
from decimal import Decimal
from statistics import NormalDist

import pytest

from src.foundation.backtest.domain import overfitting as overfitting_module
from src.foundation.backtest.domain.overfitting import (
    OVERFITTING_VERSION,
    OverfittingError,
    deflated_sharpe,
    pbo_cscv,
)
from tests.conftest import PerfBudget

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
        sr_hat=Decimal("0.5"),
        n_trials=100,
        T=250,
        skew=Decimal("-0.5"),
        kurt=Decimal("4.0"),
        sr_var=Decimal("0.04"),
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
            sr_hat=Decimal("0.5"),
            n_trials=1,
            T=250,
            skew=Decimal("0"),
            kurt=Decimal("3"),
            sr_var=Decimal("0.04"),
        )


def test_deflated_sharpe_rejects_T_below_2() -> None:
    with pytest.raises(OverfittingError):
        deflated_sharpe(
            sr_hat=Decimal("0.5"),
            n_trials=100,
            T=1,
            skew=Decimal("0"),
            kurt=Decimal("3"),
            sr_var=Decimal("0.04"),
        )


def test_deflated_sharpe_rejects_non_positive_sr_var() -> None:
    with pytest.raises(OverfittingError):
        deflated_sharpe(
            sr_hat=Decimal("0.5"),
            n_trials=100,
            T=250,
            skew=Decimal("0"),
            kurt=Decimal("3"),
            sr_var=Decimal("0"),
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


# --- DEEPEN(task-3203): 실패 주입 -- 상류(리포트/분산 계산) 손상 데이터도 fail-closed ---


def test_deflated_sharpe_rejects_nan_sr_var_from_corrupted_upstream_variance() -> None:
    """실패 주입: 상류 표본분산 계산이 0으로 나누기 등으로 손상되어 sr_var가
    `Decimal('NaN')`으로 들어오면(overfitting.py 모듈 docstring: "substituting
    0/1/None/NaN ... is forbidden"), deflated_sharpe는 그 NaN을 그대로
    Phi(...)로 흘려보내 조용히 NaN을 반환하지 않고 fail-closed 거부해야
    한다. Decimal의 NaN 비교(`sr_var <= 0`)는 CPython에서 이미
    decimal.InvalidOperation을 내므로, 이 테스트는 그 사실이 향후 리팩터링
    (예: `sr_var <= 0`을 `float(sr_var) <= 0`으로 바꾸는 변경)에도 유지되는지
    못박는다."""
    with pytest.raises(decimal.InvalidOperation):
        deflated_sharpe(
            sr_hat=Decimal("0.5"),
            n_trials=100,
            T=250,
            skew=Decimal("0"),
            kurt=Decimal("3"),
            sr_var=Decimal("NaN"),
        )


def test_pbo_cscv_rejects_float_corrupted_column_from_upstream_serialization() -> None:
    """실패 주입: 상류 성과 리포트 직렬화가 손상되어(CLAUDE.md #3 "Monetary
    amounts are Decimal, never float" 위반) perf_matrix의 값 하나가 float로
    섞여 들어오면, pbo_cscv의 열 평균 계산(`_column_mean`의
    `sum(values, Decimal(0))`)은 그 값을 조용히 섞어 잘못된 PBO를 내지 않고
    TypeError로 fail-closed 거부해야 한다."""
    matrix = [[Decimal(10), Decimal(1), Decimal(0)] for _ in range(8)]
    matrix[3][1] = 0.5  # 주입된 손상: float, Decimal 아님
    with pytest.raises(TypeError):
        pbo_cscv(matrix, 4)


# --- DEEPEN(task-3203): 수치 성능 단언 -- pbo_cscv 조합 폭발 핫 패스 ---


def _pbo_cscv_latencies_ms(perf_budget: PerfBudget, iterations: int = 30) -> list[float]:
    """task-6774 -- `time.process_time()` 기반 공용 `perf_budget` 픽스처로
    측정한다(이전 `time.perf_counter()` wall-clock은 다른 워커/로컬 추론
    프로세스에 코어를 뺏긴 시간까지 샘플에 섞여 p95를 부풀렸다). 실측 1회
    호출(~4.3ms)이 Windows `time.process_time()`의 ~15.6ms(64Hz) 양자화
    폭보다 작아 개별 호출을 그대로 재면 0ms/15.625ms 둘 중 하나로만
    읽힌다 -- `batch=8`로 8회를 한 구간에 묶어 호출당 양자화 오차를
    ~2ms로 줄인다(`perf_budget.sample` 참조)."""
    rng = random.Random(20260917)
    matrix = [[Decimal(str(rng.uniform(-1.0, 1.0))) for _ in range(8)] for _ in range(64)]
    samples = perf_budget.samples(lambda: pbo_cscv(matrix, 8), n=iterations, warmup=1, batch=8)
    return sorted(s.cpu_ms for s in samples)


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_PBO_CSCV_BUDGET_MS = 15.0


def test_pbo_cscv_p95_latency_within_self_declared_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언: pbo_cscv는 n_blocks가 커질수록 C(n_blocks, n_blocks/2)
    조합만큼 열 평균 계산을 반복하는 조합 폭발 경로다(ADR-2026-09-09-C
    예산표에 전용 항목은 없다 -- combinations 순회 + Decimal 산술뿐인 순수
    CPU 경로라는 사실 위에 자체 예산을 건다). 64행x8열/n_blocks=8
    (C(8,4)=70 조합) 기준 로컬 실측 p95(~4.3ms) 대비 넉넉한 여유를 둔
    15ms."""
    samples = _pbo_cscv_latencies_ms(perf_budget)
    p95_ms = _p95(samples)
    print(
        f"[L34 overfitting] pbo_cscv p95={p95_ms:.3f}ms "
        f"budget<{_PBO_CSCV_BUDGET_MS:.0f}ms (n={len(samples)}) "
        f"load={perf_budget.load_percent()}"
    )
    assert p95_ms < _PBO_CSCV_BUDGET_MS


def test_pbo_cscv_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """게이트 적색 재현: 위 단언식이, `_split_blocks` 경로(호출당 1회) 한
    곳이 예산을 실제로 넘기도록 지연을 주입했을 때 진짜로 AssertionError를
    내는지(= CI가 실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위
    단언이 항상 통과하는 tautology인지 아무도 검증하지 못한다."""
    original_split_blocks = overfitting_module._split_blocks

    def _stalled_split_blocks(n_rows: int, n_blocks: int) -> list[list[int]]:
        # task-6774 -- perf_budget이 `time.process_time()`(CPU 시간)으로
        # 측정하므로, `time.sleep()`(CPU를 실제로 놓아준다)로는 이 주입이
        # 측정값에 전혀 잡히지 않는다 -- busy-wait으로 실제 CPU 시간을
        # 소비해야 이 게이트 적색 재현이 여전히 유효하다.
        busy_until = time.process_time() + (_PBO_CSCV_BUDGET_MS / 1000.0)
        while time.process_time() < busy_until:
            pass
        return original_split_blocks(n_rows, n_blocks)

    monkeypatch.setattr(overfitting_module, "_split_blocks", _stalled_split_blocks)

    samples = _pbo_cscv_latencies_ms(perf_budget, iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _PBO_CSCV_BUDGET_MS
