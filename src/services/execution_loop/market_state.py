"""FD-8.1 execution loop — market_state assembly.

Only calculates the indicator keys the strategy's FSM condition expressions
actually reference (parses back the key format ConditionCompiler produces;
the grammar's single source of truth is `src.core.strategy.indicator_key`) —
never computes indicators that aren't needed.
"""
from __future__ import annotations

from src.core.indicators.talib_adapter import IndicatorService
from src.core.strategy.condition_evaluator import extract_indicator_keys
from src.core.strategy.indicator_key import IndicatorKeyError, parse_key
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.services.condition_compiler import ORDER_FILLED


class IndicatorKeyParseError(Exception):
    """ConditionCompiler가 만들지 않는 형태의 키 — 컴파일러/평가기 불일치 신호."""


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


def build_market_state(
    fsm_config: FSMStrategyConfig,
    candles: list[Candle],
    *,
    indicator_service: IndicatorService | None = None,
) -> dict[str, float]:
    """지표 데이터가 부족한 키는 그냥 빠진다(StrategyEngine이 이를
    IndicatorDataMissingError로 감지해 판단을 보류한다 — 여기서 조용히
    0 등으로 채우지 않는다)."""
    service = indicator_service or IndicatorService()
    market_state: dict[str, float] = {}
    for key in required_indicator_keys(fsm_config):
        indicator, params = parse_indicator_key(key)
        result = service.calculate(indicator, candles, **params)
        if not result.values:
            continue
        latest = result.values[-1]
        if latest is not None:
            market_state[key] = latest
    return market_state
