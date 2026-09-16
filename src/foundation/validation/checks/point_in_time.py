"""L38 -- checks/point_in_time.py: check 1, point-in-time integrity of the
replayed bar sequence.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2 row 159 / §9 L38,
hard-fail mapping §3.5-A row 1 (point_in_time): a future bar
(`close_time > as_of`), a missing source (`INTEGRITY_LINEAGE_MISSING`), or a
non-monotonic `open_time` sequence (`INTEGRITY_BAR_ORDER`) must all surface
as a hard fail, not a silent pass -- these are the same three conditions
`BarSnapshotRef`/`PointInTimeBars` are supposed to prevent upstream (L25/
L26), so this check is the last point-in-time gate before a strategy can be
judged validated. Gaps (an inter-bar interval that does not match the
sequence's modal interval) are reported as a warning only -- §3.5-A's
point_in_time hard-fail row does not list gaps, and a legitimate exchange
outage window is not itself proof of a data-integrity defect.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from src.data.models.market_data import Candle
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import compute_result_hash

CHECK_TYPE = "point_in_time"

__all__ = ["CHECK_TYPE", "run"]


def _count_gaps(bars: Sequence[Candle]) -> int:
    """A gap is any inter-bar interval that does not match the modal (most
    common) interval in the sequence."""
    if len(bars) < 3:
        return 0
    deltas: list[timedelta] = [
        bars[i + 1].open_time - bars[i].open_time for i in range(len(bars) - 1)
    ]
    counts: dict[timedelta, int] = {}
    for delta in deltas:
        counts[delta] = counts.get(delta, 0) + 1
    modal_delta = max(counts, key=lambda d: counts[d])
    return sum(1 for delta in deltas if delta != modal_delta)


def run(ctx: CheckContext) -> CheckResult:
    bars = list(ctx.bars.upto(len(ctx.bars) - 1))

    hard_fail_reasons: list[str] = []
    if not ctx.snapshot_ref.source:
        hard_fail_reasons.append("INTEGRITY_LINEAGE_MISSING")

    future_bars = [b for b in bars if b.close_time > ctx.snapshot_ref.as_of]
    if future_bars:
        hard_fail_reasons.append("INTEGRITY_FUTURE_DATA")

    non_monotonic = any(bars[i].open_time >= bars[i + 1].open_time for i in range(len(bars) - 1))
    if non_monotonic:
        hard_fail_reasons.append("INTEGRITY_BAR_ORDER")

    gap_count = _count_gaps(bars)
    warnings = (
        [f"{gap_count} bar interval(s) differ from the sequence's modal interval (gap)."]
        if gap_count
        else []
    )

    metrics: dict[str, Any] = {
        "bar_count": len(bars),
        "gap_count_bars": gap_count,
        "future_bar_count_bars": len(future_bars),
        "period_start": bars[0].open_time if bars else ctx.snapshot_ref.from_time,
        "period_end": bars[-1].close_time if bars else ctx.snapshot_ref.to_time,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
    }
    outcome = Outcome.FAIL if hard_fail_reasons else Outcome.PASS
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=outcome,
        metrics=metrics,
        warnings=warnings,
        hard_fail_reasons=hard_fail_reasons,
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )
