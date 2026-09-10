"""DataFreshnessTracker 단위테스트(R-42).

핵심 단언: 관측 0건 → `max_delay_sec`은 0이 아니라 `None`(fail-closed —
호출자가 "지연 없음"으로 오해하지 못하게), tz-naive datetime은 거부.

뒤쪽 `Test*InstrumentedAdapter*` 클래스는 `InstrumentedAdapter`의
`freshness` 옵션 인자(R-42) 배선을 여기서 함께 검증한다 —
`tests/unit/exchanges/test_instrumented_adapter.py`는 기존 계측 동작
회귀 방지용으로 한 줄도 건드리지 않는다(DoD 3).

DEEPEN(task-2826, DEPTH_R_EO 감사): 이 리프는 "관측 트래커"일 뿐 자체
게이트가 아니라서 §9 D2의 "게이트 적색 재현"을 직접 만족할 수 없다 —
대신 트래커가 실제로 계산한 `max_delay_sec()` 값을 순수함수
`compute_level`(circuit_breaker.py, DB 불필요)에 그대로 흘려 넣어
다운스트림 게이트가 실제로 적색(HALTED)이 되는 것까지 증명한다
(`test_stale_observation_drives_circuit_breaker_to_halted`). 여기에
실패 주입(`test_get_ohlcv_failure_does_not_record_and_propagates`),
성능 단언(`test_max_delay_sec_scales_to_many_symbols_within_budget`),
다중 인스턴스 동시성(`test_concurrent_instrumented_adapters_share_tracker_without_clobbering`)
을 더해 D2 네 항목을 모두 채운다.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    compute_level,
)
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


def test_stale_observation_drives_circuit_breaker_to_halted() -> None:
    """게이트 적색 재현 — `DataFreshnessTracker`는 자체 게이트가 아니므로,
    트래커가 실제로 계산한 지연값을 순수함수 `compute_level`(DB 불필요)에
    그대로 넘겨 다운스트림 Circuit Breaker가 실제로 HALTED가 됨을 증명한다.
    halted 임계(config/risk_policy.yaml: 5.0초)를 훌쩍 넘는 600초 지연."""
    policy = load_risk_policy().circuit_breaker
    tracker = DataFreshnessTracker()
    now = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    tracker.record("bitget", "BTC/USDT", now - timedelta(seconds=600))

    metrics = CircuitBreakerMetrics(data_delay_sec=tracker.max_delay_sec(now))

    assert compute_level(metrics, policy) == CircuitBreakerLevel.HALTED


class _FailingAdapter:
    def __init__(self) -> None:
        self.is_paper_trading = True

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[_FakeCandle]:
        raise ConnectionError("simulated exchange timeout")


async def test_get_ohlcv_failure_does_not_record_and_propagates() -> None:
    """실패 주입 — 어댑터 호출이 예외로 실패하면 freshness는 아무것도
    기록하지 않아야 한다(캔들이 없으니 close_time도 없다). 예외 자체는
    삼키지 않고 그대로 전파해야 호출부가 실패를 인지할 수 있다."""
    freshness = DataFreshnessTracker()
    tracker = ApiCallTracker()
    wrapped = InstrumentedAdapter(_FailingAdapter(), tracker, freshness=freshness)  # type: ignore[arg-type]

    with pytest.raises(ConnectionError, match="simulated exchange timeout"):
        await wrapped.get_ohlcv("BTC/USDT", "1m")

    assert freshness.max_delay_sec(datetime.now(timezone.utc)) is None
    assert tracker.error_rate_pct() == Decimal("100")


def test_max_delay_sec_scales_to_many_symbols_within_budget() -> None:
    """성능 단언 — 10,000개 (exchange, symbol) 쌍을 기록한 뒤에도
    `max_delay_sec`이 선형 스캔 예산(2초) 안에서 끝나야 한다. dict 갱신을
    매 record()마다 정렬하거나 전체를 재계산하는 식으로 퇴화하면(예: 매번
    O(n log n) 정렬) 이 예산을 넘긴다."""
    tracker = DataFreshnessTracker()
    now = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(10_000):
        tracker.record("bitget", f"SYM-{i}/USDT", now - timedelta(seconds=i % 50))

    started = time.perf_counter()
    result = tracker.max_delay_sec(now)
    elapsed = time.perf_counter() - started

    assert result == Decimal("49")
    assert elapsed < 2.0


class _TaggedAdapter:
    def __init__(self, exchange: str, symbol: str, close_time: datetime) -> None:
        self._exchange = exchange
        self._symbol = symbol
        self._close_time = close_time
        self.is_paper_trading = True

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[_FakeCandle]:
        # 두 인스턴스가 실제로 인터리빙되도록 강제로 한 번 양보한다.
        await asyncio.sleep(0)
        return [_FakeCandle(self._exchange, self._symbol, self._close_time)]


async def test_concurrent_instrumented_adapters_share_tracker_without_clobbering() -> None:
    """다중 인스턴스 증명 — main.py 배선처럼 서로 다른 거래소를 감싼 두
    `InstrumentedAdapter` 인스턴스가 하나의 `DataFreshnessTracker`를
    공유한다. 두 get_ohlcv 호출을 실제로 동시에(asyncio.gather) 실행해도
    (exchange, symbol) 키가 서로를 덮어쓰지 않고 둘 다 정확히 관측돼야
    한다."""
    now = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
    freshness = DataFreshnessTracker()
    tracker = ApiCallTracker()
    bitget = InstrumentedAdapter(
        _TaggedAdapter("bitget", "BTC/USDT", now - timedelta(seconds=10)),  # type: ignore[arg-type]
        tracker,
        freshness=freshness,
    )
    okx = InstrumentedAdapter(
        _TaggedAdapter("okx", "ETH/USDT", now - timedelta(seconds=200)),  # type: ignore[arg-type]
        tracker,
        freshness=freshness,
    )

    await asyncio.gather(
        bitget.get_ohlcv("BTC/USDT", "1m"),
        okx.get_ohlcv("ETH/USDT", "1m"),
    )

    assert freshness.max_delay_sec(now) == Decimal("200")
    assert freshness._last_close_time[("bitget", "BTC/USDT")] == now - timedelta(seconds=10)
    assert freshness._last_close_time[("okx", "ETH/USDT")] == now - timedelta(seconds=200)
