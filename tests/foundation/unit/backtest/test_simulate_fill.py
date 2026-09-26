"""simulate_fill() 단위테스트 — 체결가/수수료/슬리피지 계산 검증."""

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import src.foundation.backtest.application.simulate_fill as simulate_fill_module
from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.simulate_fill import simulate_fill
from src.foundation.backtest.domain.models import CostModel

_NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _bar(*, open_price: str = "100") -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=Decimal(open_price),
        low=Decimal(open_price),
        close=Decimal(open_price),
        volume=Decimal("1"),
        open_time=_NOW,
        close_time=_NOW,
    )


def test_buy_fill_pays_slippage_above_open() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("10"))  # 0.1%
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("100.1")
    assert fill.slippage_cost == Decimal("0.2")  # (100.1-100) * 2
    assert fill.fee == Decimal("0")


def test_sell_fill_receives_slippage_below_open() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("10"))
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.SELL,
        quantity=Decimal("2"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("99.9")
    assert fill.slippage_cost == Decimal("0.2")


def test_fee_applied_on_effective_price_not_base_price() -> None:
    cost_model = CostModel(fee_bps=Decimal("100"), slippage_bps=Decimal("0"))  # 1%
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=0,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("100")
    assert fill.fee == Decimal("1")


def test_fill_timestamp_and_symbol_come_from_bar() -> None:
    bar = _bar(open_price="50")
    fill = simulate_fill(
        bar=bar,
        bar_index=3,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
    )
    assert fill.timestamp == bar.open_time
    assert fill.symbol == bar.symbol
    assert fill.bar_index == 3


def test_zero_quantity_is_rejected() -> None:
    """수량 0은 체결로 성립하지 않는다 -- BarFillSimulator의 명시적 불변식."""
    with pytest.raises(ValueError, match="quantity"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        )


def test_negative_quantity_is_rejected() -> None:
    """음수 수량은 매도/매수 방향을 side로 이미 표현하므로 부호로 또
    표현하면 이중 의미가 되어 거부한다."""
    with pytest.raises(ValueError, match="quantity"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.SELL,
            quantity=Decimal("-1"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        )


def test_negative_slippage_bps_is_rejected() -> None:
    """음수 slippage_bps는 "항상 유리한 방향으로만 체결된다"는 비현실적
    가정을 인코딩하게 되므로 거부한다(46번 §2 비용모델 원칙)."""
    with pytest.raises(ValueError, match="slippage_bps"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("-10")),
        )


def test_underlying_simulator_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """simulate_fill()은 109 SS5 문서대로 BarFillSimulator에 대한 얇은
    위임 래퍼일 뿐이다 -- 어댑터 의존성이 예외를 던지면 삼키지 않고
    그대로 전파해야 한다(실패주입)."""

    class _ExplodingSimulator:
        def simulate(self, **_kwargs: object) -> None:
            raise RuntimeError("adapter dependency failure")

    monkeypatch.setattr(simulate_fill_module, "_SIMULATOR", _ExplodingSimulator())

    with pytest.raises(RuntimeError, match="adapter dependency failure"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        )


def test_simulate_fill_completes_within_latency_budget() -> None:
    """단일 체결 계산은 순수 산술이라 p95 1ms 예산을 크게 밑돌아야
    한다 -- 숫자 성능 단언(D2 floor)."""
    cost_model = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))
    bar = _bar(open_price="100")

    start = time.perf_counter()
    for i in range(200):
        simulate_fill(
            bar=bar,
            bar_index=i,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms / 200 < 1.0
