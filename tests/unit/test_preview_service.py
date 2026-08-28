"""14.4 단위테스트 — 순수 계산 로직."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.data.models.market_data import Candle
from src.services.preview_service import DISCLAIMER, PreviewCalculator, PreviewCondition


def _candles(prices: list[float]) -> list[Candle]:
    now = datetime.now(timezone.utc)
    out = []
    for i, price in enumerate(prices):
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


def test_preview_always_includes_disclaimer():
    result = PreviewCalculator().preview(_candles([100] * 20), [])
    assert result.disclaimer == DISCLAIMER


def test_no_conditions_returns_no_signals():
    result = PreviewCalculator().preview(_candles([100] * 20), [])
    assert result.signal_indices == []


def test_single_condition_flags_matching_candles():
    # RSI(2)로 짧은 기간에 상승/하락을 강제해 RSI > 90 조건이 걸리게 만든다
    prices = [100, 101, 102, 103, 104, 105, 106, 107]
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    result = PreviewCalculator().preview(_candles(prices), [condition])

    assert len(result.signal_indices) > 0


def test_and_combination_requires_all_conditions():
    prices = [100 + i for i in range(30)]
    always_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    never_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=1000
    )

    result = PreviewCalculator().preview(_candles(prices), [always_true, never_true], combine="AND")

    assert result.signal_indices == []


def test_or_combination_needs_only_one_condition():
    prices = [100 + i for i in range(30)]
    always_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    never_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=1000
    )

    result = PreviewCalculator().preview(_candles(prices), [always_true, never_true], combine="OR")

    assert len(result.signal_indices) > 0


def test_insufficient_candles_propagates_message():
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 200}, operator=">", threshold=0
    )

    result = PreviewCalculator().preview(_candles([100] * 5), [condition])

    assert result.signal_indices == []
    assert "데이터 부족" in result.message


def test_crosses_above_detects_transition():
    prices = [10, 10, 10, 20, 20]
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 1}, operator="crosses_above", threshold=15
    )

    result = PreviewCalculator().preview(_candles(prices), [condition])

    assert 3 in result.signal_indices
    assert 4 not in result.signal_indices
