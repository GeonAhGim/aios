"""R-28 — CandleHistoryCache 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-28.
DB 없이 순수 캐시 로직만 검증 — adapter는 페이크로 주입한다.

DC-28 후속(ADR-2026-09-06-H D7) — `test_get_isolates_cache_by_owner_id`가
그 DoD("사용자 A의 연결 시세가 사용자 B 응답에 포함되면 테스트가 실패한다")
를 증명한다. 나머지 기존 테스트는 `owner_id`를 명시적으로 넘기도록만
갱신했다(동작 변경 없음).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from src.data.models.market_data import Candle
from src.services.execution_loop.candle_history import CandleHistoryCache

_OWNER = uuid4()


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

    result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert result is not None
    assert len(result) == 30
    assert adapter.calls == [("BTC/USDT", 30)]


async def test_get_reuses_cache_within_ttl_for_same_bars() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    clock.advance(59)
    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert adapter.calls == [("BTC/USDT", 30)]


async def test_get_refetches_after_ttl_expires() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    clock.advance(60)
    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert adapter.calls == [("BTC/USDT", 30), ("BTC/USDT", 30)]


async def test_get_refetches_when_bars_increase_even_within_ttl() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    clock.advance(1)
    result = await cache.get(adapter, "BTC/USDT", bars=60, owner_id=_OWNER)

    assert result is not None
    assert len(result) == 60
    assert adapter.calls == [("BTC/USDT", 30), ("BTC/USDT", 60)]


async def test_get_returns_none_on_adapter_failure_without_prior_cache() -> None:
    adapter = _FakeAdapter()
    adapter.fail = True
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert result is None


async def test_get_returns_none_instead_of_stale_cache_when_refetch_fails() -> None:
    """TTL 만료 후 재조회가 실패하면, 이전에 성공적으로 캐시된(이제는
    만료된) 데이터를 대신 돌려주지 않고 None을 반환해야 한다 — 호출자가
    오래된 캔들을 최신인 것처럼 쓰는 사고를 막는다."""
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    first = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    assert first is not None

    clock.advance(60)
    adapter.fail = True
    result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert result is None


async def test_get_caches_per_symbol_independently() -> None:
    adapter = _FakeAdapter()
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    await cache.get(adapter, "ETH/USDT", bars=30, owner_id=_OWNER)

    assert adapter.calls == [("BTC/USDT", 30), ("ETH/USDT", 30)]


async def test_get_isolates_cache_by_owner_id() -> None:
    """DC-28 후속(D7) negative — 같은 (exchange, symbol)이라도 `owner_id`가
    다르면 캐시를 공유하지 않는다. 사용자 A의 연결로 가져온 캔들이 캐시
    안에 있어도, 사용자 B의 요청은 같은 항목을 돌려받는 대신 반드시 자기
    자신의 adapter로 다시 조회한다 — 이게 실패하면(호출 1회로 줄면) A의
    연결 시세가 B에게 새는 것과 같은 모양이다."""
    owner_a, owner_b = uuid4(), uuid4()
    adapter_a = _FakeAdapter()
    adapter_b = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    result_a = await cache.get(adapter_a, "AAPL", bars=30, owner_id=owner_a)
    result_b = await cache.get(adapter_b, "AAPL", bars=30, owner_id=owner_b)

    assert adapter_a.calls == [("AAPL", 30)]
    assert adapter_b.calls == [("AAPL", 30)]  # B의 요청이 A의 캐시로 대신되지 않았다.
    assert result_a is not None and result_b is not None
    assert result_a is not result_b
