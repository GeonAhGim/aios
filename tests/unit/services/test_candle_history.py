"""R-28 — CandleHistoryCache 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-28.
DB 없이 순수 캐시 로직만 검증 — adapter는 페이크로 주입한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.data.models.market_data import Candle
from src.services.execution_loop.candle_history import CandleHistoryCache


def _make_candles(n: int, *, symbol: str = "BTC/USDT") -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol=symbol,
            exchange="bitget",
            timeframe="1d",
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
            open_time=base + timedelta(days=i),
            close_time=base + timedelta(days=i + 1),
        )
        for i in range(n)
    ]


class _FakeCapabilities:
    def __init__(self, exchange_name: str) -> None:
        self.exchange_name = exchange_name


class _FakeAdapter:
    def __init__(self, exchange_name: str = "bitget") -> None:
        self._exchange_name = exchange_name
        self.calls: list[tuple[str, int]] = []
        self.fail = False

    def get_capabilities(self) -> _FakeCapabilities:
        return _FakeCapabilities(self._exchange_name)

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self.calls.append((symbol, limit))
        if self.fail:
            raise RuntimeError("adapter unreachable")
        return _make_candles(limit, symbol=symbol)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def test_get_fetches_from_adapter_on_first_call() -> None:
    adapter = _FakeAdapter()
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result = await cache.get(adapter, "BTC/USDT", bars=30)

    assert result is not None
    assert len(result) == 30
    assert adapter.calls == [("BTC/USDT", 30)]


async def test_get_reuses_cache_within_ttl_for_same_bars() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30)
    clock.advance(59)
    await cache.get(adapter, "BTC/USDT", bars=30)

    assert adapter.calls == [("BTC/USDT", 30)]


async def test_get_refetches_after_ttl_expires() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30)
    clock.advance(60)
    await cache.get(adapter, "BTC/USDT", bars=30)

    assert adapter.calls == [("BTC/USDT", 30), ("BTC/USDT", 30)]


async def test_get_refetches_when_bars_increase_even_within_ttl() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30)
    clock.advance(1)
    result = await cache.get(adapter, "BTC/USDT", bars=60)

    assert result is not None
    assert len(result) == 60
    assert adapter.calls == [("BTC/USDT", 30), ("BTC/USDT", 60)]


async def test_get_returns_none_on_adapter_failure_without_prior_cache() -> None:
    adapter = _FakeAdapter()
    adapter.fail = True
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result = await cache.get(adapter, "BTC/USDT", bars=30)

    assert result is None


async def test_get_returns_none_instead_of_stale_cache_when_refetch_fails() -> None:
    """TTL 만료 후 재조회가 실패하면, 이전에 성공적으로 캐시된(이제는
    만료된) 데이터를 대신 돌려주지 않고 None을 반환해야 한다 — 호출자가
    오래된 캔들을 최신인 것처럼 쓰는 사고를 막는다."""
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    first = await cache.get(adapter, "BTC/USDT", bars=30)
    assert first is not None

    clock.advance(60)
    adapter.fail = True
    result = await cache.get(adapter, "BTC/USDT", bars=30)

    assert result is None


async def test_get_caches_per_symbol_independently() -> None:
    adapter = _FakeAdapter()
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    await cache.get(adapter, "BTC/USDT", bars=30)
    await cache.get(adapter, "ETH/USDT", bars=30)

    assert adapter.calls == [("BTC/USDT", 30), ("ETH/USDT", 30)]
