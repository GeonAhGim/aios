"""FD-8.1 실행 루프 — market_state 조립.

전략의 FSM 조건식이 실제로 참조하는 지표 키만 계산한다(ConditionCompiler가
만든 키 형식 `"{INDICATOR}{_paramKey파라미터값}*"`을 거꾸로 파싱) — 필요
없는 지표까지 매번 계산하지 않는다.
"""
from __future__ import annotations

import re

from src.core.indicators.talib_adapter import IndicatorService
from src.core.strategy.condition_evaluator import extract_indicator_keys
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.services.condition_compiler import ORDER_FILLED

_KEY_RE = re.compile(r"^(?P<indicator>[A-Z]+)(?P<params>(?:_[a-z]+\d+)*)$")
_PARAM_RE = re.compile(r"_([a-z]+)(\d+)")


class IndicatorKeyParseError(Exception):
    """ConditionCompiler가 만들지 않는 형태의 키 — 컴파일러/평가기 불일치 신호."""


def parse_indicator_key(key: str) -> tuple[str, dict[str, int]]:
    match = _KEY_RE.match(key)
    if match is None:
        raise IndicatorKeyParseError(f"지표 키를 해석할 수 없습니다: {key!r}")
    params = {name: int(value) for name, value in _PARAM_RE.findall(match["params"])}
    return match["indicator"], params


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
