"""FD-8.1 / L14 execution loop — 다중 타임프레임 market_state 조립.

Only calculates the indicator keys the strategy's FSM condition expressions
actually reference (parses back the key format ConditionCompiler produces;
the grammar's single source of truth is `src.core.strategy.indicator_key`) —
never computes indicators that aren't needed.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 205, §9 L14.
I/O(거래소 캔들 조회)는 호출부(tick.py/run_backtest.py) 책임 — 이 모듈은
이미 가져온 `candles_by_tf`를 받아 순수 계산만 한다.
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

_DEFAULT_TIMEFRAME = "1m"  # `@tf` 없는 키(현재 ConditionCompiler 산출물)의 암묵적 tf


class IndicatorKeyParseError(Exception):
    """ConditionCompiler가 만들지 않는 형태의 키 — 컴파일러/평가기 불일치 신호."""


class MarketStateAssemblyError(Exception):
    """전략이 필요로 하는 타임프레임이 candles_by_tf에 통째로 없음(호출부의
    부분 갱신 실패) — R-32 §5 오류표 `market_state_partial`: 부분 시장상태로
    판단하지 않고 이 틱 전체를 폐기한다."""


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
    """tf별 필요 bar 수(L07 `lookback.required_bars` 위임). `@tf`가 없는
    키는 `_DEFAULT_TIMEFRAME`으로 승격해야 `required_bars`(모든 키가
    명시적 tf를 갖도록 강제)를 통과한다."""
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
    """다중 타임프레임 시장상태 조립.

    지표 데이터가 부족한 키(warm-up bar 미달)는 그냥 빠진다(StrategyEngine이
    이를 `IndicatorDataMissingError`로 감지해 판단을 보류한다 — 여기서
    조용히 0 등으로 채우지 않는다). 반대로 전략이 필요로 하는 타임프레임
    자체가 `candles_by_tf`에 아예 없으면(호출부의 tf별 갱신 부분 실패)
    개별 키만 건너뛰지 않고 `MarketStateAssemblyError`로 틱 전체를 폐기한다.
    타임프레임 존재 여부만 확인한다(등록되지 않은 커스텀 indicator를 쓰는
    `indicator_service`도 지원하기 위해 `IndicatorRegistry` lookback 조회에는
    기대지 않는다 — bar 수 산정은 `required_timeframes`의 몫).
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

    # U10 — 진행 중(아직 닫히지 않은) bar는 절대 지표 계산에 쓰지 않는다.
    # 거래소가 마지막 원소로 미종가 bar를 돌려주더라도 여기서 제외한다.
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
    assert_no_future(state)  # I1 — 방어적 이중 확인(위 U10 필터로 이미 보장됨)
    return state
