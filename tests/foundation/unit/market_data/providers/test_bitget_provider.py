"""DC-12 `adapters/providers/bitget_provider.py` 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-12(DoD: capabilities()가 실제 거래소 능력과 일치, fetch_candles가
DC-5 CandleColumns 형태를 반환하며 빈 구간·부분 응답·심볼 미존재 각각
negative test, src/exchanges/** 변경 0줄).

`BitgetAdapter`는 생성 자체는 네트워크 I/O가 없어(httpx.AsyncClient 준비만)
`capabilities()` 대조 테스트에 실제 인스턴스를 그대로 쓴다. `fetch_candles`
테스트는 실 네트워크를 타지 않도록 `get_history_candles`만 흉내 내는
페이크 어댑터를 주입한다(provider는 어댑터를 덕타이핑으로만 호출한다).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.market_data import Candle
from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.market_data.adapters.providers.bitget_provider import BitgetProvider
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.reference.symbol_normalizer import SymbolNormalizationError
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    MarketDataProvider,
    TimeSpan,
)

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeBitgetAdapter:
    """`get_history_candles`만 흉내 내는 페이크 — provider가 실제로 부르는
    딱 그 메서드만 구현해 어댑터 위임이 덕타이핑임을 드러낸다."""

    def __init__(self, candles_by_symbol: dict[str, list[Candle]]) -> None:
        self._candles = candles_by_symbol
        self.calls: list[tuple[str, str, int, str | None]] = []

    async def get_history_candles(
        self, symbol: str, timeframe: str, *, limit: int = 100, end_time: str | None = None
    ) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit, end_time))
        return list(self._candles.get(symbol, []))


def _listing(venue: Venue = Venue.BITGET, venue_symbol: str = "BTCUSDT") -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=venue,
        venue_symbol=venue_symbol,
        listed_at=_BASE - timedelta(days=1),
        delisted_at=None,
        is_primary=True,
    )


def _candle(hour: int) -> Candle:
    open_time = _BASE + timedelta(hours=hour)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1.5"),
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
    )


# ---- capabilities(): 실제 BitgetAdapter.get_capabilities()와 대조 --------


def test_capabilities_matches_real_bitget_adapter_declaration() -> None:
    real_adapter = BitgetAdapter(api_key="k", api_secret="s", api_passphrase="p")
    exchange_capability = real_adapter.get_capabilities()

    caps = BitgetProvider(_FakeBitgetAdapter({})).capabilities()

    assert caps.provider_id == "bitget"
    assert caps.asset_classes == frozenset(exchange_capability.supported_asset_classes)
    assert caps.realtime is exchange_capability.supports_websocket
    assert Timeframe.M1 in caps.timeframes and Timeframe.D1 in caps.timeframes


def test_provider_structurally_implements_market_data_provider_protocol() -> None:
    assert isinstance(BitgetProvider(_FakeBitgetAdapter({})), MarketDataProvider)


# ---- fetch_candles: happy path(부분 응답 포함) ----------------------------


async def test_fetch_candles_returns_candle_columns_filtered_to_span() -> None:
    fake = _FakeBitgetAdapter({"BTC/USDT": [_candle(h) for h in range(5)]})  # 0..4시
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE + timedelta(hours=1), end=_BASE + timedelta(hours=4))

    columns = await provider.fetch_candles(_listing(), Timeframe.H1, span)

    assert isinstance(columns, CandleColumns)
    # 부분 응답 — 어댑터가 5개를 돌려줘도 span [1,4)에 속하는 3개(1,2,3시)만.
    assert columns.ts == [_BASE + timedelta(hours=h) for h in (1, 2, 3)]
    assert columns.open == [Decimal("100")] * 3
    assert columns.quote_volume == [None, None, None]
    assert fake.calls[0][0] == "BTC/USDT"  # canonical 심볼로 위임했다


# ---- negative: 빈 구간 -----------------------------------------------------


async def test_fetch_candles_raises_coverage_missing_when_span_has_no_data() -> None:
    fake = _FakeBitgetAdapter({"BTC/USDT": [_candle(h) for h in range(5)]})
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE + timedelta(hours=10), end=_BASE + timedelta(hours=11))

    with pytest.raises(DataProviderError) as excinfo:
        await provider.fetch_candles(_listing(), Timeframe.H1, span)
    assert excinfo.value.code is DataProviderErrorCode.DATA_COVERAGE_MISSING


# ---- negative: 심볼 미존재(포맷 해석 불가, fail-closed) --------------------


async def test_fetch_candles_rejects_unrecognizable_symbol_before_network_call() -> None:
    fake = _FakeBitgetAdapter({})
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=1))
    bad_listing = _listing(venue_symbol="???")

    with pytest.raises(SymbolNormalizationError):
        await provider.fetch_candles(bad_listing, Timeframe.H1, span)
    assert fake.calls == []  # 네트워크 위임 전에 fail-closed


async def test_fetch_candles_rejects_listing_from_other_venue() -> None:
    provider = BitgetProvider(_FakeBitgetAdapter({}))
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=1))
    kis_listing = _listing(venue=Venue.KIS_KRX, venue_symbol="005930")

    with pytest.raises(ValueError, match="Venue.BITGET"):
        await provider.fetch_candles(kis_listing, Timeframe.H1, span)


# ---- 미구현 훅: fail-closed(조용한 빈 결과 대신 명시적 실패) --------------


async def test_list_instruments_fails_closed_not_implemented() -> None:
    provider = BitgetProvider(_FakeBitgetAdapter({}))
    with pytest.raises(NotImplementedError):
        await provider.list_instruments(AssetClass.CRYPTO)


async def test_subscribe_fails_closed_not_implemented() -> None:
    provider = BitgetProvider(_FakeBitgetAdapter({}))
    with pytest.raises(NotImplementedError):
        async for _ in provider.subscribe([_listing()]):
            pass
