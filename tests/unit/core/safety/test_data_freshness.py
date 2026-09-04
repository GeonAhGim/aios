"""DataFreshnessTracker 단위테스트(R-42).

핵심 단언: 관측 0건 → `max_delay_sec`은 0이 아니라 `None`(fail-closed —
호출자가 "지연 없음"으로 오해하지 못하게), tz-naive datetime은 거부.

뒤쪽 `Test*InstrumentedAdapter*` 클래스는 `InstrumentedAdapter`의
`freshness` 옵션 인자(R-42) 배선을 여기서 함께 검증한다 —
`tests/unit/exchanges/test_instrumented_adapter.py`는 기존 계측 동작
회귀 방지용으로 한 줄도 건드리지 않는다(DoD 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.safety.data_freshness import DataFreshnessTracker
from src.core.safety.metrics_collector import ApiCallTracker
from src.exchanges.common.instrumented_adapter import InstrumentedAdapter


def test_no_observations_returns_none() -> None:
    tracker = DataFreshnessTracker()

    assert tracker.max_delay_sec(datetime.now(timezone.utc)) is None


def test_record_then_max_delay_sec_reflects_elapsed_time() -> None:
    tracker = DataFreshnessTracker()
    close_time = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    now = close_time + timedelta(seconds=30)

    tracker.record("bitget", "BTC/USDT", close_time)

    assert tracker.max_delay_sec(now) == Decimal("30")


def test_max_delay_sec_takes_worst_across_symbols() -> None:
    tracker = DataFreshnessTracker()
    now = datetime(2026, 9, 4, 0, 1, 0, tzinfo=timezone.utc)
    tracker.record("bitget", "BTC/USDT", now - timedelta(seconds=5))
    tracker.record("bitget", "ETH/USDT", now - timedelta(seconds=90))

    assert tracker.max_delay_sec(now) == Decimal("90")


def test_record_rejects_naive_close_time() -> None:
    tracker = DataFreshnessTracker()

    with pytest.raises(ValueError, match="tz-aware"):
        tracker.record("bitget", "BTC/USDT", datetime(2026, 9, 4, 0, 0, 0))


def test_max_delay_sec_rejects_naive_now() -> None:
    tracker = DataFreshnessTracker()
    tracker.record("bitget", "BTC/USDT", datetime.now(timezone.utc))

    with pytest.raises(ValueError, match="tz-aware"):
        tracker.max_delay_sec(datetime(2026, 9, 4, 0, 0, 0))


def test_record_overwrites_previous_value_for_same_key() -> None:
    tracker = DataFreshnessTracker()
    now = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    tracker.record("bitget", "BTC/USDT", now - timedelta(seconds=100))
    tracker.record("bitget", "BTC/USDT", now - timedelta(seconds=1))

    assert tracker.max_delay_sec(now) == Decimal("1")


@dataclass
class _FakeCandle:
    exchange: str
    symbol: str
    close_time: datetime


class _FakeAdapterWithOhlcv:
    def __init__(self, candles: list[_FakeCandle]) -> None:
        self._candles = candles
        self.is_paper_trading = True

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[_FakeCandle]:
        return self._candles


async def test_get_ohlcv_records_last_candle_close_time_via_instrumented_adapter() -> None:
    close_time = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    candles = [
        _FakeCandle("bitget", "BTC/USDT", close_time - timedelta(minutes=1)),
        _FakeCandle("bitget", "BTC/USDT", close_time),
    ]
    freshness = DataFreshnessTracker()
    wrapped = InstrumentedAdapter(
        _FakeAdapterWithOhlcv(candles),  # type: ignore[arg-type]
        ApiCallTracker(),
        freshness=freshness,
    )

    result = await wrapped.get_ohlcv("BTC/USDT", "1m")

    assert result == candles
    assert freshness.max_delay_sec(close_time) == Decimal("0")


async def test_get_ohlcv_without_freshness_arg_does_not_raise() -> None:
    close_time = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    candles = [_FakeCandle("bitget", "BTC/USDT", close_time)]
    wrapped = InstrumentedAdapter(
        _FakeAdapterWithOhlcv(candles),  # type: ignore[arg-type]
        ApiCallTracker(),
    )

    result = await wrapped.get_ohlcv("BTC/USDT", "1m")

    assert result == candles


async def test_get_ohlcv_empty_result_does_not_record() -> None:
    freshness = DataFreshnessTracker()
    wrapped = InstrumentedAdapter(
        _FakeAdapterWithOhlcv([]),  # type: ignore[arg-type]
        ApiCallTracker(),
        freshness=freshness,
    )

    result = await wrapped.get_ohlcv("BTC/USDT", "1m")

    assert result == []
    assert freshness.max_delay_sec(datetime.now(timezone.utc)) is None


async def test_get_ohlcv_rejects_naive_close_time_from_candle() -> None:
    candles = [_FakeCandle("bitget", "BTC/USDT", datetime(2026, 9, 4, 0, 0, 0))]
    freshness = DataFreshnessTracker()
    wrapped = InstrumentedAdapter(
        _FakeAdapterWithOhlcv(candles),  # type: ignore[arg-type]
        ApiCallTracker(),
        freshness=freshness,
    )

    with pytest.raises(ValueError, match="tz-aware"):
        await wrapped.get_ohlcv("BTC/USDT", "1m")
