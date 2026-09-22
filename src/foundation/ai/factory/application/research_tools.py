"""ResearchTools -- AI-14.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-14
`application/research_tools.py` ("read/research tool implementations
(indicator computation, backtest execution, experiment lookup) -- entirely
delegated to existing use cases"), §9 AI-14 DoD ("delegation only, zero
logic"), depends on AI-11 (`experiments/application/{query,compare}.py`) and
BT-10 (`backtest/application/quick_backtest.py`).

§3 scope semantics name the two callers this leaf exists for: `read`
(indicator/data/coverage lookup) and `research` (backtest/sweep execution,
experiment lookup) -- this module is the read/research half of the Strategy
Factory's own application layer, one function per capability those two
scopes name for indicators/backtests/experiments. §1's "thin MCP"
requirement ("the MCP server holds no more authorization/logic than REST
already enforces") is why AI-15's future `src/api/mcp/tools_{read,
research}.py` calls straight into this module rather than the three modules
below directly: this leaf is the one place an MCP tool handler imports from,
and AI-4's `authorize(token, scope, resource)` gate runs in that MCP-server
layer, not here -- this leaf has no `AgentToken`/scope argument at all,
matching the AI-14 row's own dependency list (AI-11, BT-10; not AI-4).

Every function below is a direct call-through, not a re-implementation:
`compute_indicator` -> `engine/vectorized.compute` (IND-1, indicator math
single source of truth), `run_backtest` -> `quick_backtest.run_quick_backtest`
(BT-10, fill-model assembly), and the five experiment functions ->
`experiments/application/{query,compare}.py` (AI-11, the module whose own
docstring already names this leaf as its intended agent-facing caller). No
function here adds a branch, a default, or a retry the callee does not
already have -- the DoD's "zero logic" is enforced by every function body
being exactly one `return await`/`return` of the delegate call. Error types
the delegates raise are re-exported (not caught/renamed) so a caller needs
only this module's import surface to handle them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

import numpy as np

from src.core.indicators.engine.vectorized import compute as _compute_indicator
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError, IndicatorRegistry
from src.foundation.backtest.application.quick_backtest import (
    MAX_QUICK_BARS,
    LookAheadError,
    QuickBacktestInputError,
    QuickBacktestResult,
    SignalSource,
    TooManyBarsError,
)
from src.foundation.backtest.application.quick_backtest import (
    run_quick_backtest as _run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.experiments.application.compare import (
    CorruptedReproductionSetError,
    ExperimentComparison,
    NoReproductionsFoundError,
    ReproducibilityKeyMismatchError,
)
from src.foundation.experiments.application.compare import (
    compare_by_reproducibility_key as _compare_by_reproducibility_key,
)
from src.foundation.experiments.application.compare import (
    compare_experiments as _compare_experiments,
)
from src.foundation.experiments.application.query import ExperimentNotFoundError
from src.foundation.experiments.application.query import get_experiment as _get_experiment
from src.foundation.experiments.application.query import get_lineage_chain as _get_lineage_chain
from src.foundation.experiments.application.query import list_reproductions as _list_reproductions
from src.foundation.experiments.contracts.v1 import Experiment
from src.foundation.experiments.ports.repository import ExperimentRepository
from src.foundation.market_data.api import CandleColumns
from src.foundation.market_data.contracts.v1 import Timeframe

__all__ = [
    "MAX_QUICK_BARS",
    "CorruptedReproductionSetError",
    "ExperimentComparison",
    "ExperimentNotFoundError",
    "IndicatorError",
    "LookAheadError",
    "NoReproductionsFoundError",
    "QuickBacktestInputError",
    "QuickBacktestResult",
    "ReproducibilityKeyMismatchError",
    "SignalSource",
    "TooManyBarsError",
    "compare_experiment_reproductions",
    "compare_experiment_set",
    "compute_indicator",
    "get_experiment_context",
    "get_experiment_lineage",
    "list_experiment_reproductions",
    "run_backtest",
]

_IndicatorColumns = Mapping[
    str, Sequence[float | int | Decimal] | np.ndarray[Any, np.dtype[np.float64]]
]


def compute_indicator(
    name: str,
    columns: _IndicatorColumns,
    params: Mapping[str, int] | None = None,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> dict[str, np.ndarray[Any, np.dtype[np.float64]]]:
    """`read` scope MCP tool implementation. Delegates as-is to
    `engine/vectorized.compute` (IND-1) -- param range validation, lookback
    determination, and output array shape are that module's responsibility."""
    return _compute_indicator(name, columns, params, registry)


def run_backtest(
    config: BacktestConfigV2,
    columns: CandleColumns,
    *,
    timeframe: Timeframe,
    strategy: SignalSource,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
    lower_columns: CandleColumns | None = None,
    max_bars: int = MAX_QUICK_BARS,
) -> QuickBacktestResult:
    """`research` scope MCP tool implementation. Delegates as-is to
    `quick_backtest.run_quick_backtest` (BT-10) -- the fill model, look-ahead
    guard, and max-bar cap are that module's responsibility."""
    return _run_quick_backtest(
        config,
        columns,
        timeframe=timeframe,
        strategy=strategy,
        initial_cash=initial_cash,
        funding_rate=funding_rate,
        lower_columns=lower_columns,
        max_bars=max_bars,
    )


async def get_experiment_context(
    repository: ExperimentRepository, tenant_id: UUID, experiment_id: UUID
) -> Experiment:
    """`research` scope MCP tool implementation. Delegates as-is to
    `query.get_experiment` (AI-11) -- cross-tenant lookup keeps that module's
    own `WHERE tenant_id` judgment."""
    return await _get_experiment(repository, tenant_id, experiment_id)


async def list_experiment_reproductions(
    repository: ExperimentRepository, tenant_id: UUID, reproducibility_key: str
) -> tuple[Experiment, ...]:
    """`research` scope MCP tool implementation. Delegates as-is to
    `query.list_reproductions` (AI-11)."""
    return await _list_reproductions(repository, tenant_id, reproducibility_key)


async def get_experiment_lineage(
    repository: ExperimentRepository, tenant_id: UUID, experiment_id: UUID
) -> tuple[Experiment, ...]:
    """`research` scope MCP tool implementation. Delegates as-is to
    `query.get_lineage_chain` (AI-11)."""
    return await _get_lineage_chain(repository, tenant_id, experiment_id)


async def compare_experiment_set(
    repository: ExperimentRepository, tenant_id: UUID, experiment_ids: Sequence[UUID]
) -> ExperimentComparison:
    """`research` scope MCP tool implementation. Delegates as-is to
    `compare.compare_experiments` (AI-11) -- the reproducibility_key/
    inputs_hash identity check is that module's responsibility."""
    return await _compare_experiments(repository, tenant_id, experiment_ids)


async def compare_experiment_reproductions(
    repository: ExperimentRepository, tenant_id: UUID, reproducibility_key: str
) -> ExperimentComparison:
    """`research` scope MCP tool implementation. Delegates as-is to
    `compare.compare_by_reproducibility_key` (AI-11)."""
    return await _compare_by_reproducibility_key(repository, tenant_id, reproducibility_key)
