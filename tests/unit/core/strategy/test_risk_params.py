"""L10 D2 증빙 -- src/core/strategy/risk_params.py.

negative >= 3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1
(ADR-2026-09-09-C Decision 1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L10.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.strategy.condition_evaluator import IndicatorDataMissingError
from src.core.strategy.engine import StrategyEngine
from src.core.strategy.market_state import MarketState
from src.core.strategy.risk_params import StrategyRiskParams, derive_levels
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _market_state(values: dict[str, Decimal] | None = None) -> MarketState:
    return MarketState(as_of=_AS_OF, values=values or {})


# --- 산식 정확값 -----------------------------------------------------------


def test_pct_based_stop_and_take_profit_exact_values():
    params = StrategyRiskParams(stop_loss_pct=Decimal("5"), take_profit_pct=Decimal("10"))

    stop, take_profit = derive_levels(params, Decimal("100"), _market_state())

    assert stop == Decimal("95")
    assert take_profit == Decimal("110")


def test_atr_based_stop_exact_value():
    params = StrategyRiskParams(atr_stop_multiple=Decimal("2"), atr_key="ATR_timeperiod14")
    market_state = _market_state({"ATR_timeperiod14": Decimal("3")})

    stop, take_profit = derive_levels(params, Decimal("100"), market_state)

    assert stop == Decimal("94")  # 100 - 2*3
    assert take_profit is None


def test_no_rules_configured_yields_no_levels():
    params = StrategyRiskParams()

    stop, take_profit = derive_levels(params, Decimal("100"), _market_state())

    assert stop is None
    assert take_profit is None


# --- negative (>= 3) -------------------------------------------------------


def test_negative_stop_loss_pct_at_or_above_hundred_rejected():
    with pytest.raises(ValidationError, match="stop_loss_pct must be within"):
        StrategyRiskParams(stop_loss_pct=Decimal("100"))


def test_negative_stop_loss_pct_zero_or_below_rejected():
    with pytest.raises(ValidationError, match="stop_loss_pct must be within"):
        StrategyRiskParams(stop_loss_pct=Decimal("-1"))


def test_negative_atr_multiple_without_atr_key_rejected():
    with pytest.raises(ValidationError, match="must be set together"):
        StrategyRiskParams(atr_stop_multiple=Decimal("2"))


def test_negative_pct_and_atr_mutually_exclusive_rejected():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        StrategyRiskParams(
            stop_loss_pct=Decimal("5"),
            atr_stop_multiple=Decimal("2"),
            atr_key="ATR_timeperiod14",
        )


def test_negative_entry_price_not_positive_rejected():
    params = StrategyRiskParams(stop_loss_pct=Decimal("5"))
    with pytest.raises(ValueError, match="entry_price must be positive"):
        derive_levels(params, Decimal("0"), _market_state())


def test_negative_computed_stop_not_positive_rejected():
    # ATR 배수가 지나치게 커서 stop이 0 이하로 계산되는 경우 -- 가격이 음수인
    # 손절선은 물리적으로 의미가 없으므로 조용히 통과시키지 않는다.
    params = StrategyRiskParams(atr_stop_multiple=Decimal("100"), atr_key="ATR_timeperiod14")
    market_state = _market_state({"ATR_timeperiod14": Decimal("5")})

    with pytest.raises(ValueError, match="computed stop price must be positive"):
        derive_levels(params, Decimal("100"), market_state)


# --- 실패 주입 (1) ----------------------------------------------------------


def test_failure_injection_atr_key_missing_from_market_state_fails_closed():
    """지표가 아직 워밍업되지 않았거나 잘못된 타임프레임의 market_state가
    전달된 경우 -- 손절 계산을 건너뛰고 진입을 무방비로 통과시키면 안 된다
    (§1.2 감사 인용이 지적한 "손절 부재" 결함의 재발 방지)."""
    params = StrategyRiskParams(atr_stop_multiple=Decimal("2"), atr_key="ATR_timeperiod14")
    market_state = _market_state({})  # ATR 값 없음

    with pytest.raises(IndicatorDataMissingError):
        derive_levels(params, Decimal("100"), market_state)


# --- 성능 단언 (1) ----------------------------------------------------------


@pytest.mark.perf
def test_performance_derive_levels_p99_latency_within_pretrade_gate_budget():
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms."""
    params = StrategyRiskParams(stop_loss_pct=Decimal("5"), take_profit_pct=Decimal("10"))
    market_state = _market_state()

    n = 1000
    latencies_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        derive_levels(params, Decimal("100"), market_state)
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(n * 0.99) - 1]
    assert p99 < 5.0, f"p99={p99:.3f}ms — 사전거래 게이트 5ms 예산 초과"


# --- 게이트 적색 재현 (1) ----------------------------------------------------


def test_gate_red_engine_stop_loss_and_take_profit_ignored_today():
    """§1.2 감사 인용: engine.py는 `stop_loss=None`, `take_profit=None`
    상수를 반환한다 -- 전략에 risk_params가 설정돼 있어도 무시된다(적색).
    risk_params.py는 같은 진입가·시세에서 실제 보호선을 계산한다(초록).
    배선(engine.py가 derive_levels를 실제로 호출하도록 바꾸는 것)은 이
    task의 FROZEN 승인 범위(신규 파일 신설만) 밖이라 별도 리프에서
    처리한다."""
    transitions = [
        FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="RSI < 40.0"),
    ]
    config = FSMStrategyConfig(
        strategy_id="strat-risk-gate",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=transitions,
        author_agent="test",
    )

    # 적색: engine.py는 risk_params라는 개념 자체가 없다 -- 신호가 나가도
    # stop_loss/take_profit은 항상 None이다.
    engine = StrategyEngine()
    signal = engine.evaluate(config, {"RSI": 10.0}, execution_id=9101, fsm_state=FSMState.IDLE)
    assert signal is not None
    assert signal.stop_loss is None
    assert signal.take_profit is None

    # 초록: 같은 진입가·시세를 risk_params.derive_levels에 넘기면 실제
    # 보호선이 계산된다.
    params = StrategyRiskParams(stop_loss_pct=Decimal("5"), take_profit_pct=Decimal("10"))
    stop, take_profit = derive_levels(params, Decimal("100"), _market_state())
    assert stop == Decimal("95")
    assert take_profit == Decimal("110")
