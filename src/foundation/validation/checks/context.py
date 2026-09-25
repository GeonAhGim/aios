"""L38 -- CheckContext: the read-only input bundle every checks/*.run(ctx)
function receives.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2 row 165 / §9 L38.
Field set and order are fixed by the spec's `CheckContext(artifact, policy,
bars, snapshot_ref, universe, config, seed, trace_id, prior_results)` --
this module only assembles existing domain types (L17/L25/L26/L29/L36), it
does not define a new one of its own besides the bundle itself.

`prior_results` lets a later check (e.g. failure_conditions, L40) read an
earlier check's `CheckResult` (e.g. oos_walk_forward's OOS metrics) without
re-running it.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.foundation.backtest.api import BacktestConfig, BarSnapshotRef, UniverseSnapshot
from src.foundation.backtest.ports.bar_source import PointInTimeBars
from src.foundation.validation.domain.artifact import StrategyArtifact
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.policy import ValidationPolicy

__all__ = ["CheckContext"]


@dataclass(frozen=True)
class CheckContext:
    artifact: StrategyArtifact
    policy: ValidationPolicy
    bars: PointInTimeBars
    snapshot_ref: BarSnapshotRef
    universe: UniverseSnapshot
    config: BacktestConfig
    seed: int
    trace_id: str
    prior_results: dict[str, CheckResult]
