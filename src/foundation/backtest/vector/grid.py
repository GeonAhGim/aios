"""BT-16a (1/3) — `backtest/vector/grid.py`: bulk execution of parameter-combo sweeps.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16. The original BT-16 leaf depends on BT-15 and AI-10 (experiment ledger), but AI-10
hasn't started, so the task-2371 decision split the leaf in two -- this module (grid.py)
and its sibling files `walk_forward.py`/`monte_carlo.py` handle only the bulk-execution
part; persisting per-combo results to the experiment ledger (BT-16b) is deferred until
after AI-10. This module writes nothing to a ledger -- where the caller records the
return value is the caller's responsibility.

Symmetric to how BT-17 `universe.py` swept the same `config` across instruments: this
module sweeps a different `VectorSignal` per parameter combo (generating the signal
itself is the DSL/strategy layer's job -- this leaf only receives signals, it doesn't
create them) over the same `CandleColumns`. It does not reimplement the fill arithmetic
(I-05, avoiding §C duplicate context) -- it calls BT-15b `run_vector_backtest` (delegating
to the BT-2~6 event fill engine) once per combo, unchanged.

Pure module -- no I/O.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["GridSweepResult", "sweep_grid"]


@dataclass(frozen=True, slots=True)
class GridSweepResult:
    """`results` maps the caller-assigned combo key (e.g. `"rsi_len=14,exit=20"`) to
    that combo's `QuickBacktestResult`. The key set always exactly matches the input
    `combos` key set (no combo is silently dropped) -- if even one combo fails, the
    exception `run_vector_backtest` raises propagates as-is, so no partial result is
    ever produced."""

    results: dict[str, QuickBacktestResult]


def sweep_grid(
    columns: CandleColumns,
    combos: Mapping[str, VectorSignal],
    config: BacktestConfigV2,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
) -> GridSweepResult:
    """Runs `run_vector_backtest` with the same `config` for each combo in `combos`
    (only the signal differs, over the same `columns`). An empty `combos` yields an
    empty result (same precedent as `universe.sweep_universe` allowing an empty
    universe)."""
    results = {
        key: run_vector_backtest(
            config, columns, signal, timeframe=timeframe,
            initial_cash=initial_cash, funding_rate=funding_rate,
        )
        for key, signal in combos.items()
    }
    return GridSweepResult(results=results)
