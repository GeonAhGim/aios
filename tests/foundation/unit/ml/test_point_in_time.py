"""Unit tests for `src/foundation/ml/domain/point_in_time.py` -- task-2653
AI-18 DoD ("future data rejection (A-3)"). D2 depth (ADR-2026-09-09-C):
negative >= 3, failure injection 1, numeric performance assertion 1,
gate-red reproduction 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.point_in_time import (
    FutureDataLeakageError,
    check_point_in_time,
)

_NOW = datetime.now(timezone.utc)


def _model_card(*, lineage_end: datetime, **overrides: Any) -> ModelCard:
    lineage_start = lineage_end - timedelta(days=30)
    trained_at = overrides.pop("trained_at", lineage_end)
    base: dict[str, Any] = dict(
        model_id="momentum-lgbm",
        version="1",
        model_hash="a" * 64,
        train_data_lineage=TrainDataLineage(
            start=lineage_start, end=lineage_end, source_ref="parquet://features/v1"
        ),
        trained_at=trained_at,
    )
    base.update(overrides)
    return ModelCard(**base)


# --- happy path ---


def test_check_point_in_time_accepts_lineage_strictly_before_backtest_start() -> None:
    backtest_start = _NOW
    card = _model_card(lineage_end=backtest_start - timedelta(days=1))
    check_point_in_time(card, backtest_start=backtest_start)  # no raise


# --- negative (>= 3) ---


def test_check_point_in_time_rejects_lineage_end_equal_to_backtest_start() -> None:
    backtest_start = _NOW
    card = _model_card(lineage_end=backtest_start)
    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(card, backtest_start=backtest_start)


def test_check_point_in_time_rejects_lineage_end_after_backtest_start() -> None:
    backtest_start = _NOW
    card = _model_card(lineage_end=backtest_start + timedelta(days=1))
    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(card, backtest_start=backtest_start)


def test_check_point_in_time_rejects_naive_backtest_start() -> None:
    card = _model_card(lineage_end=_NOW - timedelta(days=1))
    naive_start = _NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        check_point_in_time(card, backtest_start=naive_start)


def test_future_data_leakage_error_carries_model_id_and_bounds() -> None:
    backtest_start = _NOW
    lineage_end = backtest_start + timedelta(hours=1)
    card = _model_card(lineage_end=lineage_end)
    with pytest.raises(FutureDataLeakageError) as exc_info:
        check_point_in_time(card, backtest_start=backtest_start)
    assert exc_info.value.model_id == "momentum-lgbm"
    assert exc_info.value.lineage_end == lineage_end
    assert exc_info.value.backtest_start == backtest_start


# --- failure injection ---


def test_check_point_in_time_rejects_lineage_end_one_microsecond_after_boundary() -> None:
    """Failure injection: a lineage ending just barely (1us) past the
    backtest start must still reject -- a naive off-by-epsilon comparison
    (e.g. rounding to seconds before comparing) would let this through."""
    backtest_start = _NOW
    card = _model_card(lineage_end=backtest_start + timedelta(microseconds=1))
    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(card, backtest_start=backtest_start)


# --- numeric performance assertion ---

_CHECK_BUDGET_MS = 5.0
"""Pure in-memory comparison with no I/O -- generously wide budget for a
regression guard, not a real SLO (§7 has no ML-signal-specific number)."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
def test_check_point_in_time_p95_latency_within_budget() -> None:
    backtest_start = _NOW
    card = _model_card(lineage_end=backtest_start - timedelta(days=1))
    samples: list[float] = []
    for _ in range(200):
        started = time.perf_counter()
        check_point_in_time(card, backtest_start=backtest_start)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(f"[AI-18 check_point_in_time] p95={p95_ms:.4f}ms budget<{_CHECK_BUDGET_MS:.1f}ms")
    assert p95_ms < _CHECK_BUDGET_MS


# --- gate-red reproduction ---


def test_gate_red_lenient_comparison_would_have_leaked_boundary_case() -> None:
    """Proves the strict `<` in `check_point_in_time` is load-bearing, not
    incidental: a red implementation using `<=` (lineage end allowed to tie
    the backtest start) would have silently accepted the exact-boundary
    leak that the green implementation rejects above."""
    backtest_start = _NOW
    lineage_end = backtest_start  # exact tie

    def _red_check(end: datetime, start: datetime) -> bool:
        return end <= start  # bug: should be strict '<'

    assert _red_check(lineage_end, backtest_start) is True  # red would accept the leak
    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(_model_card(lineage_end=lineage_end), backtest_start=backtest_start)
