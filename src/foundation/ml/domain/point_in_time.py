"""AI-18 -- point-in-time rule (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-18
(`domain/point_in_time.py`: "use only data at/after training time (pure
rule) -- enforced in the backtest"), §4 A-3 ("an ML model is rejected from
a backtest call unless `train_data_lineage.end < backtest.start`"), §9
AI-18 DoD ("future data rejection (A-3)").

This is the sole guard a backtest caller (AI-21's `serve_signal`, not yet
implemented) must run before invoking a `ml.<model_id>` indicator inside a
backtest window -- fail-closed: any ambiguity (a naive `backtest_start`, an
`end` that only ties rather than strictly precedes) rejects rather than
guesses.
"""

from __future__ import annotations

from datetime import datetime

from src.foundation.ml.contracts.v1 import ModelCard

__all__ = ["FutureDataLeakageError", "check_point_in_time"]


class FutureDataLeakageError(ValueError):
    """`model_card.train_data_lineage.end` is not strictly before
    `backtest_start` -- the model was trained on data at or after the
    backtest's own start, so calling it inside that backtest would leak
    future information into the signal (spec §4 A-3)."""

    def __init__(self, model_id: str, lineage_end: datetime, backtest_start: datetime) -> None:
        self.model_id = model_id
        self.lineage_end = lineage_end
        self.backtest_start = backtest_start
        super().__init__(
            f"model {model_id!r} train_data_lineage.end={lineage_end.isoformat()} "
            f"is not before backtest.start={backtest_start.isoformat()} "
            "(A-3 future data rejection)"
        )


def check_point_in_time(model_card: ModelCard, *, backtest_start: datetime) -> None:
    """Call before invoking `model_card` inside a backtest starting at
    `backtest_start`. Raises `FutureDataLeakageError` unless
    `train_data_lineage.end < backtest_start` strictly -- equality is also
    rejected, because a lineage ending exactly at the backtest's first
    instant would still have "seen" that instant's data during training.

    `backtest_start` must be timezone-aware (CLAUDE.md §3, all datetimes
    tz-aware UTC); a naive value raises `ValueError` immediately rather than
    silently comparing against `model_card.train_data_lineage.end` (always
    aware -- `TrainDataLineage` validates that) and either raising a
    harder-to-diagnose `TypeError` or, worse, comparing as if both were the
    same wall-clock zone.
    """
    if backtest_start.tzinfo is None:
        raise ValueError("backtest_start must be timezone-aware (CLAUDE.md §3)")

    lineage_end = model_card.train_data_lineage.end
    if not (lineage_end < backtest_start):
        raise FutureDataLeakageError(model_card.model_id, lineage_end, backtest_start)
