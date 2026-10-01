"""ML integration-test package boundary -- negative/failure-injection/DoD checks.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-18/AI-19 DoD.
ADR-2026-09-09-C D2: negative >= 3, failure injection 1, numeric performance
assertion 1, gate-red reproduction 1.

Covers the pure-domain rules (`domain/feature_values.py`, `domain/point_in_time.py`,
`domain/drift.py`, `domain/registry_rules.py`) plus a DB-level gate-red
reproduction of the `ml_model_registry` migration's CHECK constraint
(`f85e5d761d6b`). Sibling `test_postgres_model_registry.py` already covers the
adapter end-to-end; this module targets the domain rules it reuses, so none
of the scenarios below duplicate that file's cases.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import pytest

from src.foundation.ml.contracts.v1 import FeatureSpec, ModelCard, TrainDataLineage
from src.foundation.ml.domain.drift import (
    InsufficientSamplesError,
    evaluate_drift,
    population_stability_index,
)
from src.foundation.ml.domain.feature_values import (
    InvalidFeatureValueError,
    validate_feature_value,
)
from src.foundation.ml.domain.point_in_time import FutureDataLeakageError, check_point_in_time
from src.foundation.ml.domain.registry_rules import (
    ModelHashMismatchError,
    validate_new_registration,
)
from tests.conftest import PerfBudget

_NOW = datetime.now(timezone.utc)


def _card(**overrides: Any) -> ModelCard:
    base: dict[str, Any] = dict(
        model_id="m-dummy",
        version="v1",
        model_hash="a" * 64,
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
        ),
        trained_at=_NOW,
    )
    base.update(overrides)
    return ModelCard(**base)


# ---------------------------------------------------------------------------
# negative (>= 3): validate_feature_value rejects malformed string shapes
# ---------------------------------------------------------------------------


def test_validate_float_rejects_nan() -> None:
    """A non-finite `nan` must not be accepted for a float feature -- it
    would poison any mean/std a later caller computes over the partition."""
    spec = FeatureSpec(feature_id="f1", dtype="float", source_ref="s3://x")

    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(spec, "nan")


def test_validate_int_rejects_non_numeric() -> None:
    """Non-numeric strings must be rejected for int dtype."""
    spec = FeatureSpec(feature_id="f2", dtype="int", source_ref="s3://x")

    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(spec, "not-an-int")


def test_validate_bool_rejects_non_canonical_value() -> None:
    """Only the literal strings 'true'/'false' are accepted for bool dtype --
    '1' must not be silently coerced."""
    spec = FeatureSpec(feature_id="f3", dtype="bool", source_ref="s3://x")

    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(spec, "1")


def test_validate_category_rejects_blank_value() -> None:
    """A whitespace-only string must be rejected for category dtype."""
    spec = FeatureSpec(feature_id="f4", dtype="category", source_ref="s3://x")

    with pytest.raises(InvalidFeatureValueError):
        validate_feature_value(spec, "   ")


# ---------------------------------------------------------------------------
# negative: check_point_in_time (AI-18 A-3 future data rejection)
# ---------------------------------------------------------------------------


def test_check_point_in_time_rejects_leaked_future_data() -> None:
    """A model trained on data ending at/after the backtest start must be
    rejected -- calling it inside that backtest would leak future data."""
    card = _card(
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=10), end=_NOW - timedelta(days=1), source_ref="s3://x"
        ),
        trained_at=_NOW,
    )

    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(card, backtest_start=_NOW - timedelta(days=2))


def test_check_point_in_time_rejects_naive_backtest_start() -> None:
    """A naive (non-tz-aware) `backtest_start` must raise `ValueError`
    immediately rather than silently comparing against an aware datetime
    (CLAUDE.md §3: all datetimes are tz-aware UTC)."""
    card = _card()

    with pytest.raises(ValueError, match="timezone-aware"):
        check_point_in_time(card, backtest_start=datetime(2025, 1, 1))  # noqa: DTZ001


# ---------------------------------------------------------------------------
# negative: drift statistics reject undersized/degenerate samples
# ---------------------------------------------------------------------------


def test_population_stability_index_rejects_insufficient_samples() -> None:
    """PSI must reject a baseline with fewer than the minimum sample count
    rather than return a misleadingly precise number off a handful of
    points."""
    baseline = [1.0, 2.0, 3.0]  # below the 10-sample floor
    current = [1.0] * 20

    with pytest.raises(InsufficientSamplesError):
        population_stability_index(baseline, current)


def test_population_stability_index_rejects_zero_variance_baseline() -> None:
    """A baseline where every value is identical cannot be binned into
    quantiles -- PSI must fail closed instead of dividing by zero."""
    baseline = [1.0] * 20
    current = [1.0, 2.0, 3.0] * 10

    with pytest.raises(InsufficientSamplesError):
        population_stability_index(baseline, current)


# ---------------------------------------------------------------------------
# negative: registry_rules rejects a conflicting re-registration (pure domain)
# ---------------------------------------------------------------------------


def test_validate_new_registration_rejects_hash_mismatch() -> None:
    """Re-registering the same (model_id, version) under a different
    model_hash must raise -- it is either a build-reproducibility bug or an
    attempted silent weight swap."""
    existing = _card(model_hash="a" * 64)
    candidate = _card(model_hash="b" * 64)

    with pytest.raises(ModelHashMismatchError):
        validate_new_registration(candidate, existing=existing)


# ---------------------------------------------------------------------------
# failure injection: a corrupted float parser must still fail closed
# ---------------------------------------------------------------------------


def test_validate_feature_value_fails_closed_when_float_parser_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the underlying `float()` builtin raises something other than the
    expected `ValueError` (simulating a corrupted runtime/parser), the
    caller must see that failure rather than a silently-accepted value."""
    spec = FeatureSpec(feature_id="f-monkey", dtype="float", source_ref="s3://x")

    def _broken_float(_value: str) -> float:
        raise RuntimeError("simulated parser corruption")

    monkeypatch.setattr("builtins.float", _broken_float)

    with pytest.raises(RuntimeError, match="simulated parser corruption"):
        validate_feature_value(spec, "1.0")


# ---------------------------------------------------------------------------
# numeric performance assertion
# ---------------------------------------------------------------------------

_VALIDATE_FEATURE_P95_BUDGET_MS = 1.0
"""Single feature validation is pure Python string parsing -- comfortably
sub-millisecond; generous relative to the Windows process-time clock
quantization noted in `tests/conftest.py::PerfBudget`."""


def test_validate_feature_value_p95_within_budget(perf_budget: PerfBudget) -> None:
    """`validate_feature_value` p95 CPU time must stay under budget."""
    spec = FeatureSpec(feature_id="f-perf", dtype="float", source_ref="s3://x")

    sample = perf_budget.assert_within(
        lambda: validate_feature_value(spec, "1.0"),
        budget_ms=_VALIDATE_FEATURE_P95_BUDGET_MS,
        n=20,
        batch=50,
        label="validate_feature_value",
    )
    assert sample.cpu_ms >= 0.0


# ---------------------------------------------------------------------------
# gate-red reproduction: ml_model_registry CHECK (train_lineage_end >= start)
# ---------------------------------------------------------------------------


@pytest.fixture
async def _pool() -> asyncpg.Pool:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    yield pool
    await pool.close()


async def test_gate_red_lineage_end_before_start_check_constraint(_pool: asyncpg.Pool) -> None:
    """Direct SQL bypassing `TrainDataLineage`'s own Pydantic validator must
    still hit the migration's CHECK constraint (defense in depth, migration
    `f85e5d761d6b`)."""
    async with _pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO ml_model_registry "
                "(model_id, version, model_hash, train_lineage_start, train_lineage_end, "
                " train_lineage_source_ref, trained_at, metrics, drift_baseline) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, '{}'::jsonb, '{}'::jsonb)",
                f"m-gate-red-{time.perf_counter_ns()}",
                "v1",
                "c" * 64,
                _NOW,
                _NOW - timedelta(days=1),  # end < start -- violates the CHECK
                "s3://bad",
                _NOW,
            )


# ---------------------------------------------------------------------------
# invariant cross-check: evaluate_drift flags degraded on a clear shift
# ---------------------------------------------------------------------------


def test_evaluate_drift_flags_degraded_on_clear_distribution_shift() -> None:
    """`evaluate_drift` must flag `degraded=True` once PSI/KS cross the
    default thresholds on an obviously shifted distribution."""
    baseline = [0.0] * 50 + [1.0] * 50
    current = [2.0] * 50 + [3.0] * 50

    result = evaluate_drift("feat_shift", baseline, current)

    assert result.degraded is True
    assert result.psi >= 0.0
