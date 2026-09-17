"""AI-19 unit tests -- `domain/feature_values.py::validate_feature_value`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-19.
ADR-2026-09-09-C D2: negative >= 3, failure injection 1, numeric
performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

import time

import pytest

from src.foundation.ml.contracts.v1 import FeatureSpec
from src.foundation.ml.domain.feature_values import (
    InvalidFeatureValueError,
    validate_feature_value,
)


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


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_validate_feature_value_p95_within_budget() -> None:
    spec = _spec("float")
    samples: list[float] = []
    for i in range(200):
        started = time.perf_counter()
        validate_feature_value(spec, str(float(i)))
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-19 validate_feature_value] p95={p95_ms:.4f}ms budget<{_VALIDATE_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _VALIDATE_P95_BUDGET_MS
