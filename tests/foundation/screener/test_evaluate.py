"""UX-6 `domain/evaluate.py` unit tests (pure, no I/O).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/run_screen.py`("execution ... result cache") — the evaluator is
the part of that leaf that gives a compiled `QueryPlan` an actual meaning
against one row of field values.
"""

from __future__ import annotations

from decimal import Decimal

from src.foundation.screener.contracts.v1 import IndicatorFilter, ScreenDefinition
from src.foundation.screener.domain.evaluate import (
    RowFieldMissingError,
    ScreenerEvaluationError,
    matches_filters,
    required_field_names,
    sort_key,
    validate_plan_supported,
)
from src.foundation.screener.domain.query_plan import build_query_plan


def _plan(condition: str, *, sort_field: str | None = None):
    from src.foundation.screener.contracts.v1 import SortSpec

    definition = ScreenDefinition(
        universe="KIS_KRX",
        filters=(IndicatorFilter(condition=condition),),
        sort=SortSpec(field=sort_field, direction="asc") if sort_field else None,
    )
    return build_query_plan(definition)


# ---- happy path: comparison/and/or/arithmetic/not all evaluate ----


def test_matches_filters_evaluates_comparison() -> None:
    plan = _plan("close > 100 and volume < 1000")
    assert matches_filters(plan, {"close": Decimal("150"), "volume": Decimal("500")}) is True
    assert matches_filters(plan, {"close": Decimal("50"), "volume": Decimal("500")}) is False


def test_matches_filters_evaluates_or_and_not() -> None:
    plan = _plan("not (close < 100 or volume > 1000)")
    assert matches_filters(plan, {"close": Decimal("150"), "volume": Decimal("10")}) is True
    assert matches_filters(plan, {"close": Decimal("50"), "volume": Decimal("10")}) is False


def test_matches_filters_evaluates_arithmetic() -> None:
    plan = _plan("high - low > 5")
    assert matches_filters(plan, {"high": Decimal("110"), "low": Decimal("100")}) is True
    assert matches_filters(plan, {"high": Decimal("102"), "low": Decimal("100")}) is False


def test_required_field_names_includes_sort_field() -> None:
    plan = _plan("close > 100", sort_field="volume")
    assert required_field_names(plan) == frozenset({"close", "volume"})


def test_sort_key_reads_sort_field() -> None:
    plan = _plan("close > 100", sort_field="volume")
    assert sort_key(plan, {"close": Decimal("1"), "volume": Decimal("42")}) == Decimal("42")


# ---- negative: unknown field name (no IND-12 registry yet -> fail closed) ----


def test_validate_plan_supported_rejects_unknown_field() -> None:
    plan = _plan("per < 15")  # 'per' (PER 배수) is a fundamental field, not OHLCV
    try:
        validate_plan_supported(plan)
        raise AssertionError("expected ScreenerEvaluationError")
    except ScreenerEvaluationError as exc:
        assert exc.code == "SCREENER_EVAL_UNKNOWN_FIELD"


# ---- negative: unsupported function call (ta.* needs IND-12/series history) ----


def test_validate_plan_supported_rejects_unsupported_call() -> None:
    plan = _plan("ta.rsi(close, 14) < 30")
    try:
        validate_plan_supported(plan)
        raise AssertionError("expected ScreenerEvaluationError")
    except ScreenerEvaluationError as exc:
        assert exc.code == "SCREENER_EVAL_UNSUPPORTED_CALL"


# ---- negative: series-history postfix indexing is not a point-in-time concept ----


def test_validate_plan_supported_rejects_postfix_index() -> None:
    plan = _plan("close[1] > close")
    try:
        validate_plan_supported(plan)
        raise AssertionError("expected ScreenerEvaluationError")
    except ScreenerEvaluationError as exc:
        assert exc.code == "SCREENER_EVAL_UNSUPPORTED_POSTFIX"


# ---- failure injection: a row missing a required field raises instead of
# silently treating it as 0/false (fail-closed, mirrors HotPostgresStorage) ----


def test_matches_filters_raises_on_missing_field() -> None:
    plan = _plan("close > 100")
    try:
        matches_filters(plan, {})
        raise AssertionError("expected RowFieldMissingError")
    except RowFieldMissingError as exc:
        assert exc.field_name == "close"


# ---- gate-red repro: math.* allow-list actually rejects an unlisted call ----


def test_validate_plan_supported_rejects_unlisted_math_call() -> None:
    plan = _plan("math.sqrt(close) > 10")
    try:
        validate_plan_supported(plan)
        raise AssertionError("expected ScreenerEvaluationError")
    except ScreenerEvaluationError as exc:
        assert exc.code == "SCREENER_EVAL_UNSUPPORTED_CALL"


def test_validate_plan_supported_accepts_allow_listed_math_call() -> None:
    plan = _plan("math.abs(close) > 10")
    validate_plan_supported(plan)  # must not raise
