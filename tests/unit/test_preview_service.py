"""14.4 단위테스트 — 순수 계산 로직."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.indicators.talib_adapter import IndicatorResult
from src.data.models.market_data import Candle
from src.services.preview_service import DISCLAIMER, PreviewCalculator, PreviewCondition


class _TruncatedIndicatorService:
    """실패 주입: 캔들 개수보다 짧은 values를 돌려주는 손상된 지표 백엔드를
    흉내낸다(예: 상류 API가 부분 응답을 캐시하다 회귀로 잘라 보내는 경우)."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> IndicatorResult:
        return IndicatorResult(
            indicator=indicator, values=[1.0] * (len(candles) - 2), params=params
        )


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
    # registry(L01/L02)의 timeperiod 최소값이 2라 SMA(1)은 더 이상 유효하지
    # 않다 — SMA(2)로 바꾸면 lookback 1만큼 밀려 교차 지점이 index4가 된다.
    prices = [10, 10, 10, 20, 20]
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator="crosses_above", threshold=15
    )

    result = PreviewCalculator().preview(_candles(prices), [condition])

    assert 4 in result.signal_indices
    assert 3 not in result.signal_indices


def test_truncated_indicator_backend_fails_closed_instead_of_silent_partial_signal():
    """IndicatorService가 캔들 수보다 짧은 values를 돌려주는 백엔드 회귀가
    나면, PreviewCalculator는 남은 캔들들을 조용히 '조건 불만족'으로 넘기며
    잘못된 부분 신호를 내보내지 않고 즉시 크래시한다(fail-closed) — 신호를
    과소 계산해 사용자에게 거짓 안전 신호를 주는 것보다 낫다."""
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    calc = PreviewCalculator(indicator_service=_TruncatedIndicatorService())

    with pytest.raises(IndexError):
        calc.preview(_candles([100 + i for i in range(10)]), [condition])


def test_unknown_indicator_raises_rejection():
    """알 수 없는 지표명은 즉시 예외로 거부해야 한다 (fail-closed).
    I-07: 검증 게이트의 hard-fail 조건은 도메인 코드가 실제로 FAIL을
    반환해야 한다."""
    from src.core.indicators.registry import IndicatorError

    condition = PreviewCondition(indicator="NONEXISTENT_INDICATOR", operator=">", threshold=50)

    with pytest.raises(IndicatorError, match="STRATEGY_INDICATOR_UNKNOWN"):
        PreviewCalculator().preview(_candles([100 + i for i in range(20)]), [condition])


def test_empty_candles_list_returns_no_signals():
    """캔들 목록이 비어있으면 신호 없이 종료해야 한다 (fail-closed)."""
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    result = PreviewCalculator().preview([], [condition])

    assert result.signal_indices == []
    assert result.message is not None


def test_indicator_service_raises_on_calculate():
    """의존성(IndicatorService)가 계산 중 예외를 raise하면,
    PreviewCalculator는 예외를 전파하여 fail-closed 한다.
    실패주입: monkeypatch로 calculate를 손상시켜 Exception 유발."""
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator=">", threshold=0
    )

    class _FailingIndicatorService:
        def calculate(
            self, indicator: str, candles: list[Candle], **params: int
        ) -> IndicatorResult:
            raise RuntimeError("backend connection lost")

    calc = PreviewCalculator(indicator_service=_FailingIndicatorService())

    with pytest.raises(RuntimeError, match="backend connection lost"):
        calc.preview(_candles([100 + i for i in range(10)]), [condition])
