"""BT-10c/BT-18 — `POST /v1/backtests/{quick,sweep}` request/response schema.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.5 BT-10,
§9.9 BT-16/BT-18, §3.4(`BacktestConfigV2`).

Identifier rules follow LA-24 (`src/api/schemas/market_data.py`/`application/read_api.
resolve_instrument`): prefer `instrument_id` when present, fall back to
`venue`+`symbol`. The length cap on `script_source` reuses the same value
as DSL-12 (`scripts.MAX_SOURCE_CHARS`) — transport caps are not defined
separately in two places.

`initial_cash`/`funding_rate` are direct copies of the required/optional
arguments from `run_quick_backtest` (BT-10) — values absent from
`BacktestConfigV2` (BT-1), so they are carried separately in the request.

Responses mirror `QuickBacktestResult` (BT-10) as-is. `Decimal` fields rely
on Pydantic v2 JSON mode default behaviour (string serialisation) — no
custom encoder needed. `fills` preserve the original tuple order (by
execution time, deterministic) without re-sorting.

`Sweep*` schemas (BT-18, task-7774) map 1:1 to `SweepRequestInput`/
`SweepResultView` in frontend/packages/api-client/src/clients/backtests.ts
(camelCase<->snake_case conversion is automatic via http.ts
`keysToSnake`/`keysToCamel`). The server-side sweep execution introduces no
new domain logic: each combo repeats the exact `/quick` pattern
(`compile_source`+`build_script_signal_source`+`run_quick_backtest`, BT-10c),
and reproducibility keys come from the existing
`vector/experiment_ledger.py::record_grid_entry` (BT-16b). We do not call
`vector/grid.py::sweep_grid_and_record` because it takes a pre-built
`VectorSignal` array, not a script — no DSL-script-to-`VectorSignal`
compilation bridge exists yet, and building one is out of this leaf's scope
(API layer only; see task-7774 attempt-3 note "scope must be reduced").
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from src.api.schemas.scripts import MAX_SOURCE_CHARS
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.application.quick_backtest_fill import FillEvent, OrderType
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.market_data.contracts.v1 import Timeframe, Venue

__all__ = [
    "FillView",
    "QuickBacktestRequest",
    "QuickBacktestResultView",
    "SweepAxis",
    "SweepCombo",
    "SweepMetric",
    "SweepPointResultView",
    "SweepRequest",
    "SweepResultView",
    "SweepStabilityView",
]


class QuickBacktestRequest(BaseModel):
    venue: Venue
    symbol: str | None = None
    instrument_id: UUID | None = None
    timeframe: Timeframe
    start: AwareDatetime
    end: AwareDatetime
    as_of: AwareDatetime | None = None
    initial_cash: Decimal = Field(gt=0)
    funding_rate: Decimal | None = None
    config: BacktestConfigV2
    script_source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)


class FillView(BaseModel):
    bar_index: int
    open_time: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal
    commission: Decimal
    remaining_quantity: Decimal

    @classmethod
    def from_fill(cls, fill: FillEvent) -> FillView:
        return cls(
            bar_index=fill.bar_index,
            open_time=fill.open_time,
            side=fill.side,
            order_type=fill.order_type,
            quantity=fill.quantity,
            price=fill.price,
            commission=fill.commission,
            remaining_quantity=fill.remaining_quantity,
        )


class QuickBacktestResultView(BaseModel):
    fills: list[FillView]
    equity_curve: list[Decimal]
    final_equity: Decimal
    cash: Decimal
    position_quantity: Decimal
    funding_cost: Decimal
    borrow_cost: Decimal
    bars: int
    expired_orders: int
    warnings: list[str]

    @classmethod
    def from_result(cls, result: QuickBacktestResult) -> QuickBacktestResultView:
        return cls(
            fills=[FillView.from_fill(f) for f in result.fills],
            equity_curve=list(result.equity_curve),
            final_equity=result.final_equity,
            cash=result.cash,
            position_quantity=result.position_quantity,
            funding_cost=result.funding_cost,
            borrow_cost=result.borrow_cost,
            bars=result.bars,
            expired_orders=result.expired_orders,
            warnings=list(result.warnings),
        )


# BT-18(task-7774) parameter grid sweep. `SweepMetric` is the subset of
# `QuickBacktestResultView`'s Decimal fields usable for heatmap/stability
# comparison; the name is used verbatim as the attribute name for
# `getattr(QuickBacktestResult, metric)`, so the router needs no separate
# mapping table.
SweepMetric = Literal["final_equity", "cash", "position_quantity", "funding_cost", "borrow_cost"]


class SweepAxis(BaseModel):
    """One axis of `param_stability.ParamGrid.axes` -- `values` must be
    ascending and duplicate-free (`ParamGrid.__post_init__` invariant, which
    the router delegates to as-is)."""

    name: str
    values: list[int]


class SweepCombo(BaseModel):
    """One grid point already materialized as a compilable script --
    `axis_values` holds one value per name from a subset of `SweepRequest.axes`
    (a named representation of `param_stability.Point`). `script_hash` is
    trusted as given by the caller (the frontend's own compilation result) --
    same contract as `vector/experiment_ledger.py::record_grid_entry`; we do
    not recompute a hash here."""

    combo_key: str = Field(min_length=1)
    axis_values: dict[str, int]
    script_hash: str = Field(min_length=1)
    script_source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)


class SweepRequest(BaseModel):
    venue: Venue
    symbol: str | None = None
    instrument_id: UUID | None = None
    timeframe: Timeframe
    start: AwareDatetime
    end: AwareDatetime
    as_of: AwareDatetime | None = None
    initial_cash: Decimal = Field(gt=0)
    funding_rate: Decimal | None = None
    config: BacktestConfigV2
    axes: list[SweepAxis]
    combos: list[SweepCombo]
    metric: SweepMetric
    data_lineage_hash: str = Field(min_length=1)
    rollup_version: str = Field(min_length=1)
    seed: int


class SweepPointResultView(BaseModel):
    combo_key: str
    combo_index: int
    axis_values: dict[str, int]
    metric_value: Decimal
    reproducibility_key: str
    seed: int


class SweepStabilityView(BaseModel):
    best_axis_values: dict[str, int]
    neighbor_mean: Decimal
    neighbor_std: Decimal
    isolated: bool


class SweepResultView(BaseModel):
    axes: list[SweepAxis]
    metric: str
    points: list[SweepPointResultView]
    stability: SweepStabilityView | None
    warnings: list[str]
