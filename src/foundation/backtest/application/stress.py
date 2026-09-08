"""L35 -- `backtest/application/stress.py`: cost/slippage/gap/worst-day stress
scenarios, plus the `REQUIRED_SCENARIOS` policy constant.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L35 (contract: #2 table
`stress.py` row; `REQUIRED_SCENARIOS` = #3 policy row, line 390). Each scenario
deterministically mutates `base_config` and/or `bars`, then re-runs `run_backtest`
unchanged (I-05 -- never reimplements fill arithmetic).

Phase 1 is long-only (round-trip PnL assumes BUY-then-SELL, see
`compute_metrics._round_trip_pnls`), so `GAP_2PCT` applies a uniform 2% *adverse*
(down) gap to every bar's open -- worse fills for the long entries this phase
actually produces. Documented simplification, not verified against a two-sided
(long+short) book -- a future short-side phase would need a signed gap direction.

`WORST_5_DAYS_REMOVED` removes the 5 bars with the worst close-over-close return
(not calendar days -- `BacktestConfig` v1 carries no timeframe field to convert bar
count to day count, so "day" here means "bar"; documented simplification, matches
the constant name literally rather than guessing a timeframe).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, BacktestMetrics

__all__ = ["REQUIRED_SCENARIOS", "StressError", "StressReport", "run_stress"]

REQUIRED_SCENARIOS: tuple[str, ...] = (
    "COST_X2", "COST_X3", "SLIPPAGE_PLUS_50BPS", "WORST_5_DAYS_REMOVED", "GAP_2PCT",
)

_GAP_PCT = Decimal("0.02")
_WORST_DAYS_REMOVED_N = 5
_SLIPPAGE_PLUS_BPS = Decimal("50")


class StressError(ValueError):
    """Fail-closed rejection -- an unknown scenario name, or too few bars to remove
    `_WORST_DAYS_REMOVED_N` of them from."""


@dataclass(frozen=True, slots=True)
class StressReport:
    per_scenario: dict[str, BacktestMetrics]
    missing: list[str]


def run_stress(
    base_config: BacktestConfig,
    fsm_config: FSMStrategyConfig,
    bars: list[Candle],
    scenarios: list[str],
    *,
    indicator_service: IndicatorService | None = None,
) -> StressReport:
    per_scenario: dict[str, BacktestMetrics] = {}
    for name in scenarios:
        config, scenario_bars = _apply_scenario(name, base_config, bars)
        per_scenario[name] = run_backtest(
            config, fsm_config, scenario_bars, indicator_service=indicator_service
        ).metrics
    missing = [name for name in REQUIRED_SCENARIOS if name not in per_scenario]
    return StressReport(per_scenario=per_scenario, missing=missing)


def _apply_scenario(
    name: str, base_config: BacktestConfig, bars: list[Candle],
) -> tuple[BacktestConfig, list[Candle]]:
    if name == "COST_X2":
        return _scaled_cost(base_config, Decimal(2)), bars
    if name == "COST_X3":
        return _scaled_cost(base_config, Decimal(3)), bars
    if name == "SLIPPAGE_PLUS_50BPS":
        cost = base_config.cost_model.model_copy(
            update={"slippage_bps": base_config.cost_model.slippage_bps + _SLIPPAGE_PLUS_BPS}
        )
        return base_config.model_copy(update={"cost_model": cost}), bars
    if name == "WORST_5_DAYS_REMOVED":
        return base_config, _remove_worst_days(bars, _WORST_DAYS_REMOVED_N)
    if name == "GAP_2PCT":
        return base_config, _apply_gap(bars, _GAP_PCT)
    raise StressError(f"unknown stress scenario: {name!r}")


def _scaled_cost(base_config: BacktestConfig, multiplier: Decimal) -> BacktestConfig:
    cost = base_config.cost_model
    scaled = cost.model_copy(update={
        "fee_bps": cost.fee_bps * multiplier, "slippage_bps": cost.slippage_bps * multiplier,
    })
    return base_config.model_copy(update={"cost_model": scaled})


def _remove_worst_days(bars: list[Candle], n: int) -> list[Candle]:
    if len(bars) <= n:
        raise StressError(f"bars 개수({len(bars)})가 제거할 개수({n}) 이하다")
    returns = [
        (curr.close - prev.close) / prev.close if prev.close > 0 else Decimal("0")
        for prev, curr in zip(bars, bars[1:], strict=False)
    ]
    worst_indices = {
        index + 1 for index, _ in sorted(enumerate(returns), key=lambda pair: pair[1])[:n]
    }
    return [bar for index, bar in enumerate(bars) if index not in worst_indices]


def _apply_gap(bars: list[Candle], pct: Decimal) -> list[Candle]:
    factor = Decimal(1) - pct
    gapped: list[Candle] = []
    for bar in bars:
        new_open = bar.open * factor
        gapped.append(bar.model_copy(update={"open": new_open, "low": min(bar.low, new_open)}))
    return gapped
