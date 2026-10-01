"""Domain model for backtest simulation engine — pure data without DB/HTTP.

Spec: AIOSproject 109_backtest_simulation_engine_l3_build_and_operational_
specification_v1.0.md §3, §5; docs/specs/L4_strategy_portfolio_backtest_v1.0.md
§2.4 (`domain/models.py` row -- MINOR extension of `CostModel`/`BacktestConfig`/
`BacktestMetrics`, existing fields unchanged).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.core.risk.hashing import canonical_json, sha256_hex
from src.data.models.trading import OrderSide


class CostModel(BaseModel):
    """§46 "Backtest" row — backtests without a cost model are rejected,
    so no default is provided (forcing the caller to choose explicitly).

    v1 supports only a linear model (fixed bps) — non-linear slippage
    based on order-book depth / market impact is a future revision target
    (§46 "Capacity" row).

    v2 (MINOR, §2.4): adds maker/taker split, spread, a non-linear slippage
    choice (`SQRT_IMPACT`), a market-impact coefficient and funding cost.
    The existing `fee_bps` remains the taker-fee alias for backward
    compatibility -- callers that do not set `maker_fee_bps`/`taker_fee_bps`
    inherit `fee_bps` for both (an explicit inheritance, not a silent
    zero-fill)."""

    fee_bps: Decimal
    slippage_bps: Decimal
    maker_fee_bps: Decimal | None = None
    taker_fee_bps: Decimal | None = None
    spread_bps: Decimal = Decimal("0")
    slippage_model: Literal["LINEAR", "SQRT_IMPACT"] = "LINEAR"
    impact_coeff: Decimal | None = None
    funding_bps_per_period: Decimal = Decimal("0")

    @model_validator(mode="after")
    def _default_maker_taker_fees(self) -> CostModel:
        if self.maker_fee_bps is None:
            self.maker_fee_bps = self.fee_bps
        if self.taker_fee_bps is None:
            self.taker_fee_bps = self.fee_bps
        return self

    def cost_model_hash(self) -> str:
        """L25 -- sha256 hex of the canonical JSON (R-01 `canonical_json`) of
        every field.

        Reuses `src.core.risk.hashing` instead of inventing a new hash
        recipe -- the same "identical values yield identical bytes" contract
        as DSL-12 `script_hash` and BT-9 `config_hash`.
        `src.core.portfolio.config.CostModelRef` stores this value as its
        reference key."""
        return sha256_hex(canonical_json(self.model_dump(mode="python")))


class BacktestConfig(BaseModel):
    """All inputs needed for one playback run — pinned (fixed) before
    execution per the standard-105 rule. `warmup_bars` excludes the
    pre-stable period of indicators from signal evaluation
    (e.g. SMA(20) requires at least 20).

    v2 (MINOR): adds reproducibility inputs (`seed`, `timeframe`,
    `data_snapshot_hash`) and fill/survivorship policy switches. All get
    defaults so existing callers' instances stay valid (107 §3.3 MINOR
    rule)."""

    strategy_id: str
    strategy_version: str
    initial_equity: Decimal
    cost_model: CostModel
    warmup_bars: int = Field(ge=0)
    periods_per_year: int = Field(gt=0)
    """Sharpe/Sortino annualization factor — caller sets it to match the
    bar timeframe (e.g. 252 for daily bars, 365*24 for 1-hour bars). The
    engine must not parse the timeframe string to guess — a wrong guess
    silently produces wrong metrics (§46 "unit/annualization convention"
    must be documented principle)."""
    seed: int = 0
    timeframe: str | None = None
    data_snapshot_hash: str | None = None
    fill_policy: Literal["NEXT_OPEN", "NEXT_OPEN_WITH_GAP_CHECK"] = "NEXT_OPEN"
    survivorship_policy: Literal["UNIVERSE_SNAPSHOT_REQUIRED"] = "UNIVERSE_SNAPSHOT_REQUIRED"

    def config_hash(self) -> str:
        """Reuses R-01 -- canonical hash of every field, including `cost_model`."""
        return sha256_hex(canonical_json(self.model_dump(mode="python")))


class SimulatedFill(BaseModel):
    bar_index: int
    timestamp: datetime
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    slippage_cost: Decimal


class EquityPoint(BaseModel):
    bar_index: int
    timestamp: datetime
    equity: Decimal
    drawdown_pct: Decimal


class BacktestMetrics(BaseModel):
    """§76 "no bare float metrics" rule — every value must carry its unit
    and period in the field name. `sharpe_ratio`/`sortino_ratio` are
    `None` when the sample has fewer than 2 observations or the standard
    deviation is zero (minimum implementation of the "boundaries /
    assumptions" required by §46 — never silently returns 0).

    v2 (MINOR): adds gross/net split, cost totals, calmar, exposure time,
    annualization factor and the reporting `basis` -- filled in by L31
    (`compute_metrics.py`). `total_funding` stays `None`: Phase 1
    `simulate_fill.py` does not yet apply funding cost per fill (BT-8's
    `domain/costs/funding.py` exists but is not wired into the fill loop),
    so there is nothing to sum -- "not yet computed" stays distinct from
    zero rather than guessing."""

    period_start: datetime
    period_end: datetime
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    gross_return_pct: Decimal | None = None
    net_return_pct: Decimal | None = None
    total_fees: Decimal | None = None
    total_slippage: Decimal | None = None
    total_funding: Decimal | None = None
    calmar_ratio: Decimal | None = None
    exposure_time_pct: Decimal | None = None
    annualization: int | None = None
    basis: Literal["PAPER_SIM"] = "PAPER_SIM"


class BacktestResult(BaseModel):
    config: BacktestConfig
    fills: list[SimulatedFill]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
    warnings: list[str] = Field(default_factory=list)
