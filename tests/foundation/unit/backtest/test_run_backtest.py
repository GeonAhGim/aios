"""run_backtest() 통합 단위테스트 — 실제 TA-Lib 대신 가짜 IndicatorService로
"PRICE" 키(=해당 창의 마지막 종가)만 제공해 오케스트레이션(체결 타이밍,
FSM 전이, equity 계산)을 지표 계산과 분리해서 검증한다. 개별 수치 공식
(체결가/수수료, Sharpe 등)은 test_simulate_fill.py/test_compute_metrics.py가
이미 독립적으로 검증한다 — 여기서는 "제대로 연결됐는가"만 본다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.services.condition_compiler import ORDER_FILLED

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    """"PRICE" 키를 창(window)의 마지막 종가로 되돌린다 — TA-Lib 의존 없음."""

    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _bar(*, open_price: str, close_price: str, index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)),
        close=Decimal(close_price),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE,
            FSMState.BUY_ORDER_PENDING,
            FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 105",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition=ORDER_FILLED,
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.SELL_ORDER_PENDING,
                condition="PRICE < 95",
            ),
            FSMTransition(
                from_state=FSMState.SELL_ORDER_PENDING,
                to_state=FSMState.IDLE,
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    )


def _bars() -> list[Candle]:
    return [
        _bar(index=0, open_price="100", close_price="100"),   # 무신호
        _bar(index=1, open_price="100", close_price="110"),   # BUY 신호 발생(PRICE>105)
        _bar(index=2, open_price="112", close_price="111"),   # BUY 체결(다음 bar 시가)
        _bar(index=3, open_price="111", close_price="90"),    # SELL 신호 발생(PRICE<95)
        _bar(index=4, open_price="88", close_price="89"),     # SELL 체결(다음 bar 시가)
    ]


def _config(*, warmup_bars: int = 0) -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=warmup_bars,
        periods_per_year=252,
    )


def test_fills_execute_one_bar_after_signal_not_on_signal_bar() -> None:
    result = run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )
    assert len(result.fills) == 2
    assert result.fills[0].side == OrderSide.BUY
    assert result.fills[0].bar_index == 2  # 신호는 bar 1에서 났지만 체결은 bar 2
    assert result.fills[0].price == Decimal("112")  # bar2의 시가(개장가)
    assert result.fills[1].side == OrderSide.SELL
    assert result.fills[1].bar_index == 4
    assert result.fills[1].price == Decimal("88")


def test_position_fully_closed_after_round_trip() -> None:
    result = run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )
    buy_qty = result.fills[0].quantity
    sell_qty = result.fills[1].quantity
    assert buy_qty == sell_qty  # Phase 1 — 전량청산만 허용
    assert result.metrics.total_trades == 1


def test_equity_curve_has_one_point_per_bar() -> None:
    bars = _bars()
    result = run_backtest(
        _config(), _fsm_config(), bars, indicator_service=_FakePriceIndicatorService()
    )
    assert len(result.equity_curve) == len(bars)


def test_zero_cost_model_produces_warning() -> None:
    result = run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )
    assert any("fee_bps=0" in w for w in result.warnings)


def test_warmup_suppresses_signal_before_threshold_bar() -> None:
    """warmup_bars=2로 두면 bar 1(원래 BUY 신호가 나던 bar)이 평가 대상에서
    빠진다 — bar 1의 signal이 사라지므로 이후 체결도 원래보다 늦게(또는
    아예 없이) 일어나야 한다."""
    result = run_backtest(
        _config(warmup_bars=2),
        _fsm_config(),
        _bars(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert all(fill.bar_index != 2 for fill in result.fills if fill.side == OrderSide.BUY)
