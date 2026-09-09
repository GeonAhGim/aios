"""L14 -- src/services/execution_loop/market_state.py DoD (task-2520).

다중 타임프레임 조립, `@tf` 없는 키의 1m 승격, U10(진행 중 bar 제외),
부분 실패(필요 tf 통째로 없음) 시 예외 전파를 확인한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.services.execution_loop.market_state import (
    MarketStateAssemblyError,
    build_market_state,
    required_timeframes,
)

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candles(count: int, *, timeframe: str, step: timedelta, start: datetime) -> list[Candle]:
    out = []
    for i in range(count):
        close_time = start + step * (i + 1)
        price = Decimal(10 + i)
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe=timeframe,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
                open_time=close_time - step,
                close_time=close_time,
            )
        )
    return out


def _fsm(condition: str) -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="s1",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE, to_state=FSMState.BUY_ORDER_PENDING, condition=condition
            )
        ],
        author_agent="tester",
    )


def test_build_market_state_assembles_multiple_timeframes() -> None:
    # `@tf` 없는 키는 1m으로 승격, `@1h` 키는 명시된 tf 그대로.
    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(5, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    candles_1h = _candles(5, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = max(candles_1m[-1].close_time, candles_1h[-1].close_time)

    state = build_market_state(fsm, {"1m": candles_1m, "1h": candles_1h}, as_of=as_of)

    assert "SMA_timeperiod3" in state.values
    assert "SMA_timeperiod3@1h" in state.values
    assert state.bar_close_time["1m"] == candles_1m[-1].close_time
    assert state.bar_close_time["1h"] == candles_1h[-1].close_time
    assert state.as_of == as_of


def test_required_timeframes_defaults_missing_tf_suffix_to_1m() -> None:
    fsm = _fsm("SMA_timeperiod3 >= 1")
    assert required_timeframes(fsm) == {"1m": 3}


def test_build_market_state_excludes_unclosed_trailing_bar() -> None:
    # U10 -- 마지막 bar가 as_of 이후에 닫히면(진행 중) 지표 계산에서 제외한다.
    fsm = _fsm("SMA_timeperiod3@1h >= 1")
    closed = _candles(4, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = closed[-1].close_time
    in_progress = Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("99"),
        high=Decimal("99"),
        low=Decimal("99"),
        close=Decimal("99"),
        volume=Decimal("1"),
        open_time=as_of,
        close_time=as_of + timedelta(hours=1),
    )

    state = build_market_state(fsm, {"1h": [*closed, in_progress]}, as_of=as_of)

    assert state.bar_close_time["1h"] == closed[-1].close_time  # not the in-progress bar
    assert "SMA_timeperiod3@1h" in state.values


def test_build_market_state_skips_key_with_insufficient_warmup_without_raising() -> None:
    # tf는 candles_by_tf에 있지만 warm-up bar가 모자라면 조용히 빠진다
    # (StrategyEngine이 IndicatorDataMissingError로 판단을 보류한다).
    fsm = _fsm("SMA_timeperiod3 >= 1")
    candles = _candles(1, timeframe="1m", step=timedelta(minutes=1), start=_BASE)

    state = build_market_state(fsm, {"1m": candles}, as_of=candles[-1].close_time)

    assert state.values == {}


def test_build_market_state_raises_when_required_timeframe_entirely_missing() -> None:
    # 부분 실패(네트워크 분리로 특정 tf 갱신 실패)는 부분 시장상태로
    # 판단하지 않고 예외로 틱 전체를 폐기한다(R-32 §5 market_state_partial).
    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(5, timeframe="1m", step=timedelta(minutes=1), start=_BASE)

    with pytest.raises(MarketStateAssemblyError, match="1h"):
        build_market_state(fsm, {"1m": candles_1m}, as_of=candles_1m[-1].close_time)
