"""Unit tests for src/services/condition_evaluation.py — compare_value()."""

from __future__ import annotations

from typing import Any

import pytest

from src.services.condition_evaluation import compare_value
from tests.conftest import PerfBudget


@pytest.mark.parametrize(
    ("value", "operator", "threshold", "prev_value", "expected"),
    [
        (10.0, ">", 5.0, None, True),
        (5.0, ">", 5.0, None, False),
        (3.0, "<", 5.0, None, True),
        (5.0, "<", 5.0, None, False),
        (5.0, ">=", 5.0, None, True),
        (4.9, ">=", 5.0, None, False),
        (5.0, "<=", 5.0, None, True),
        (5.1, "<=", 5.0, None, False),
        (5.0, "==", 5.0, None, True),
        (5.01, "==", 5.0, None, False),
        (6.0, "crosses_above", 5.0, 4.0, True),
        (6.0, "crosses_above", 5.0, 5.0, True),
        (4.0, "crosses_above", 5.0, 4.0, False),
        (4.0, "crosses_below", 5.0, 6.0, True),
        (4.0, "crosses_below", 5.0, 5.0, True),
        (6.0, "crosses_below", 5.0, 6.0, False),
    ],
)
def test_compare_value_operators(
    value: float, operator: str, threshold: float, prev_value: float | None, expected: bool
) -> None:
    assert compare_value(value, operator, threshold, prev_value) is expected


def test_crosses_above_without_prev_value_is_false() -> None:
    assert compare_value(6.0, "crosses_above", 5.0, None) is False


def test_crosses_below_without_prev_value_is_false() -> None:
    assert compare_value(4.0, "crosses_below", 5.0, None) is False


def test_unsupported_operator_raises_value_error() -> None:
    with pytest.raises(ValueError, match="지원하지 않는 연산자입니다"):
        compare_value(1.0, "!=", 1.0, None)


def test_empty_string_operator_raises_value_error() -> None:
    with pytest.raises(ValueError):
        compare_value(1.0, "", 1.0, None)


def test_none_like_operator_string_raises_value_error() -> None:
    with pytest.raises(ValueError):
        compare_value(1.0, "None", 1.0, None)


def test_incompatible_value_type_propagates_type_error() -> None:
    """Failure injection: a non-numeric value must not be silently coerced —
    the comparison should raise instead of returning a wrong verdict."""
    bad_value: Any = "not-a-number"
    with pytest.raises(TypeError):
        compare_value(bad_value, ">", 5.0, None)


@pytest.mark.perf
def test_compare_value_perf_budget(perf_budget: PerfBudget) -> None:
    """Pure in-process comparison; budget is generous relative to any real
    indicator-evaluation call site (no I/O in this module)."""
    perf_budget.assert_within(
        lambda: compare_value(6.0, "crosses_above", 5.0, 4.0),
        budget_ms=1.0,
        label="compare_value",
    )
