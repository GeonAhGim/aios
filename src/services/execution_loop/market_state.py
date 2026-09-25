"""FD-8.1 / L14 execution loop — multi-timeframe market_state assembly.

Only calculates the indicator keys the strategy's FSM condition expressions
actually reference (parses back the key format ConditionCompiler produces;
the grammar's single source of truth is `src.core.strategy.indicator_key`) —
never computes indicators that aren't needed.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 205, §9 L14.
I/O (fetching exchange candles) is the caller's (tick.py/run_backtest.py)
responsibility — this module only does pure computation over the
`candles_by_tf` it is already given.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from src.core.indicators.lookback import required_bars
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.indicators.talib_adapter import IndicatorService
from src.core.strategy.condition_evaluator import extract_indicator_keys
from src.core.strategy.indicator_key import IndicatorKey, IndicatorKeyError, format_key, parse_key
from src.core.strategy.market_state import MarketState, assert_no_future
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.services.condition_compiler import ORDER_FILLED

_DEFAULT_TIMEFRAME = "1m"  # implicit tf for keys without `@tf` (current ConditionCompiler output)


class IndicatorKeyParseError(Exception):
    """A key shape ConditionCompiler never produces — signals a compiler/evaluator mismatch."""


class MarketStateAssemblyError(Exception):
    """A timeframe the strategy needs is entirely missing from candles_by_tf
    (a partial-update failure on the caller's side) — R-32 §5 error table's
    `market_state_partial`: this discards the whole tick rather than treating
    it as a partial market state."""


def parse_indicator_key(key: str) -> tuple[str, dict[str, int]]:
    try:
        parsed = parse_key(key)
    except IndicatorKeyError as exc:
        raise IndicatorKeyParseError(f"지표 키를 해석할 수 없습니다: {key!r}") from exc
    return parsed.indicator, parsed.params


def required_indicator_keys(fsm_config: FSMStrategyConfig) -> set[str]:
    keys: set[str] = set()
    for transition in fsm_config.transitions:
        if transition.condition == ORDER_FILLED:
            continue
        keys.update(extract_indicator_keys(transition.condition))
    return keys


def required_timeframes(
    fsm_config: FSMStrategyConfig, registry: IndicatorRegistry | None = None
) -> dict[str, int]:
    """Required bar count per tf (delegated to L07 `lookback.required_bars`).
    A key without `@tf` must be promoted to `_DEFAULT_TIMEFRAME` first to pass
    `required_bars` (which forces every key to have an explicit tf)."""
    normalized: list[str] = []
    for key in required_indicator_keys(fsm_config):
        parsed = parse_key(key)
        if parsed.timeframe is None:
            parsed = IndicatorKey(
                parsed.indicator, parsed.params, parsed.output, _DEFAULT_TIMEFRAME
            )
        normalized.append(format_key(parsed))
    return required_bars(normalized, registry or DEFAULT_REGISTRY)


def build_market_state(
    fsm_config: FSMStrategyConfig,
    candles_by_tf: Mapping[str, Sequence[Candle]],
    *,
    as_of: datetime,
    indicator_service: IndicatorService | None = None,
) -> MarketState:
    """Assemble the multi-timeframe market state.

    A key with insufficient indicator data (warm-up bars not met) is simply
    left out (StrategyEngine detects this as `IndicatorDataMissingError` and
    defers judgment — this function never silently fills it with 0 or
    similar). Conversely, if a timeframe the strategy needs is entirely
    absent from `candles_by_tf` (a partial per-tf update failure on the
    caller's side), the whole tick is discarded via
    `MarketStateAssemblyError` rather than skipping just that key. Only
    presence of the timeframe is checked here (this does not rely on
    `IndicatorRegistry` lookback lookups, so that an `indicator_service`
    using an unregistered custom indicator is still supported — bar-count
    sizing is `required_timeframes`'s job).
    """
    needed_tfs = {
        parse_key(key).timeframe or _DEFAULT_TIMEFRAME
        for key in required_indicator_keys(fsm_config)
    }
    missing_tfs = sorted(needed_tfs - candles_by_tf.keys())
    if missing_tfs:
        raise MarketStateAssemblyError(
            f"필요한 타임프레임의 캔들이 없습니다(market_state_partial): {missing_tfs}"
        )

    # U10 — an in-progress (not yet closed) bar must never be used in indicator
    # calculations. Excluded here even if the exchange returns an unclosed
    # bar as the last element.
    closed_by_tf: dict[str, list[Candle]] = {
        tf: [c for c in candles if c.close_time <= as_of] for tf, candles in candles_by_tf.items()
    }

    service = indicator_service or IndicatorService()
    values: dict[str, Decimal] = {}
    bar_close_time: dict[str, datetime] = {}
    for key in required_indicator_keys(fsm_config):
        parsed = parse_key(key)
        tf = parsed.timeframe or _DEFAULT_TIMEFRAME
        candles = closed_by_tf.get(tf, [])
        if not candles:
            continue
        result = service.calculate(parsed.indicator, candles, **parsed.params)
        if parsed.output is not None:
            series = result.series.get(parsed.output) if result.series else None
            latest = series[-1] if series else None
        else:
            latest = result.values[-1] if result.values else None
        if latest is None:
            continue
        values[key] = Decimal(str(latest))
        bar_close_time[tf] = candles[-1].close_time

    state = MarketState(as_of=as_of, values=values, bar_close_time=bar_close_time)
    assert_no_future(state)  # I1 — defensive double-check (already guaranteed by U10 filter above)
    return state
