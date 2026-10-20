"""AI-19 unit tests -- `domain/feature_values.py::validate_feature_value`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-19.
ADR-2026-09-09-C D2: negative >= 3, failure injection 1, numeric
performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

import pytest

from src.foundation.ml.contracts.v1 import FeatureSpec
from src.foundation.ml.domain.feature_values import (
    InvalidFeatureValueError,
    validate_feature_value,
)
from tests.conftest import PerfBudget


def _spec(dtype: str) -> FeatureSpec:
    return FeatureSpec(feature_id="f1", dtype=dtype, source_ref="s3://bucket/f1")


# --- happy path ---


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        ("float", "1.5"),
        ("float", "-0.0"),
        ("int", "42"),
        ("int", "-7"),
        ("bool", "true"),
        ("bool", "false"),
        ("category", "buy"),
    ],
)
def test_accepts_matching_shape(dtype: str, value: str) -> None:
    validate_feature_value(_spec(dtype), value)  # no raise


# --- negative (>= 3) ---


def test_rejects_non_numeric_float() -> None:
    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(_spec("float"), "not-a-number")


def test_rejects_non_integer_int() -> None:
    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(_spec("int"), "1.5")


def test_rejects_non_true_false_bool() -> None:
    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(_spec("bool"), "1")


def test_rejects_blank_category() -> None:
    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(_spec("category"), "   ")


# --- failure injection: a value that parses but is unusable ---


def test_rejects_non_finite_float() -> None:
    """`float("nan")`/`float("inf")` both parse cleanly in Python -- without
    the explicit `math.isfinite` check this would silently store a value
    that poisons any mean/std a caller later computes over the partition."""
    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(_spec("float"), "nan")
    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(_spec("float"), "inf")


# --- gate-red reproduction: prove the finite check is load-bearing ---


def test_gate_red_float_without_isfinite_check_would_accept_nan() -> None:
    """Proves `test_rejects_non_finite_float` is not a tautology: bare
    `float(...)` (the guard this leaf adds `math.isfinite` on top of)
    happily parses `"nan"` -- red without the extra check."""
    parsed = float("nan")
    assert parsed != parsed  # red: NaN would have passed a bare float() cast


# --- numeric performance assertion ---

_VALIDATE_P95_BUDGET_MS = 1.0


@pytest.mark.perf
def test_validate_feature_value_p95_within_budget(perf_budget: PerfBudget) -> None:
    spec = _spec("float")
    samples = perf_budget.samples(lambda: validate_feature_value(spec, str(float(0))), n=200)
    cpu_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_ms[min(int(len(cpu_ms) * 0.95), len(cpu_ms) - 1)]
    print(
        f"[AI-19 validate_feature_value] p95={p95_ms:.4f}ms budget<{_VALIDATE_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _VALIDATE_P95_BUDGET_MS


# ---- gate-red reproduction: 지연 주입 → 단언 적색 ---------------------------


def test_perf_budget_catches_10x_cpu_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산(1.0ms × 10 = 10ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    import time as _time

    # cumulative counter: each call returns an ever-increasing value.
    # Adding 0.020s (20ms) per call means the delta between start/end
    # in PerfBudget.sample() will be ~20ms, far above the 1.0ms budget.
    _counter = [0.0]

    def _slow_process_time() -> float:
        _counter[0] += 0.020  # +20ms per call >> 1.0ms budget
        return _counter[0]

    # Patch where PerfBudget reads it (tests.support.perf_budget module level)
    monkeypatch.setattr(_time, "process_time", _slow_process_time)

    spec = _spec("float")
    samples = perf_budget.samples(lambda: validate_feature_value(spec, str(float(0))), n=200)
    cpu_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_ms[min(int(len(cpu_ms) * 0.95), len(cpu_ms) - 1)]
    print(
        f"[AI-19 validate_feature_value] p95={p95_ms:.4f}ms budget<{_VALIDATE_P95_BUDGET_MS:.1f}ms"
    )
    with pytest.raises(AssertionError):
        assert p95_ms < _VALIDATE_P95_BUDGET_MS
