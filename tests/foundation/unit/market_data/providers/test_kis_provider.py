"""DC-12 `adapters/providers/kis_provider.py` 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-12(DoD: capabilities()가 실제 거래소 능력과 일치, fetch_candles가
DC-5 CandleColumns 형태를 반환하며 빈 구간·부분 응답·심볼 미존재 각각
negative test, src/exchanges/** 변경 0줄).

`test_bitget_provider.py`와 동일 패턴 — `KISAdapter` 생성은 네트워크 I/O가
없어(OAuth 토큰은 첫 `_request` 호출 시점에 지연 발급) `capabilities()`
대조 테스트에 실 인스턴스를 쓴다. `fetch_candles`는 `get_ohlcv`만 흉내
내는 페이크로 네트워크를 피한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.market_data import Candle
from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.adapters.providers.kis_provider import KISProvider
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


class _FakeKISAdapter:
    """`get_ohlcv`만 흉내 내는 페이크."""

    def __init__(self, candles_by_symbol: dict[str, list[Candle]]) -> None:
        self._candles = candles_by_symbol
        self.calls: list[tuple[str, str, int]] = []

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit))
        return list(self._candles.get(symbol, []))


def _listing(venue: Venue = Venue.KIS_KRX, venue_symbol: str = "005930") -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=venue,
        venue_symbol=venue_symbol,
        listed_at=_BASE - timedelta(days=1),
        delisted_at=None,
        is_primary=True,
    )


def _candle(day: int) -> Candle:
    open_time = _BASE + timedelta(days=day)
    return Candle(
        symbol="005930",
        exchange="kis",
        timeframe="1d",
        open=Decimal("70000"),
        high=Decimal("71000"),
        low=Decimal("69000"),
        close=Decimal("70500"),
        volume=Decimal("1000000"),
        open_time=open_time,
        close_time=open_time,
    )


# ---- capabilities(): 실제 KISAdapter.get_capabilities()와 대조 -----------


def test_capabilities_matches_real_kis_adapter_declaration() -> None:
    real_adapter = KISAdapter(app_key="k", app_secret="s", cano="12345678", acnt_prdt_cd="01")
    exchange_capability = real_adapter.get_capabilities()

    caps = KISProvider(_FakeKISAdapter({})).capabilities()

    assert caps.provider_id == "kis"
    assert caps.asset_classes == frozenset(exchange_capability.supported_asset_classes)
    assert caps.realtime is exchange_capability.supports_websocket
    # KISMarketDataMixin.get_ohlcv는 일봉/분봉만 지원(02d 스펙 §2) — 그 이상을
    # 미리 선언하지 않는다(capability-gated 원칙).
    assert caps.timeframes == frozenset({Timeframe.M1, Timeframe.D1})


def test_provider_structurally_implements_market_data_provider_protocol() -> None:
    assert isinstance(KISProvider(_FakeKISAdapter({})), MarketDataProvider)


# ---- fetch_candles: happy path(부분 응답 포함) ----------------------------


async def test_fetch_candles_returns_candle_columns_filtered_to_span() -> None:
    fake = _FakeKISAdapter({"005930": [_candle(d) for d in range(5)]})  # 0..4일
    provider = KISProvider(fake)
    span = TimeSpan(start=_BASE + timedelta(days=1), end=_BASE + timedelta(days=4))

    columns = await provider.fetch_candles(_listing(), Timeframe.D1, span)

    assert isinstance(columns, CandleColumns)
    assert columns.ts == [_BASE + timedelta(days=d) for d in (1, 2, 3)]
    assert columns.close == [Decimal("70500")] * 3
    assert columns.quote_volume == [None, None, None]
    assert fake.calls[0][0] == "005930"


# ---- negative: 빈 구간 -----------------------------------------------------


async def test_fetch_candles_raises_coverage_missing_when_span_has_no_data() -> None:
    fake = _FakeKISAdapter({"005930": [_candle(d) for d in range(5)]})
    provider = KISProvider(fake)
    span = TimeSpan(start=_BASE + timedelta(days=30), end=_BASE + timedelta(days=31))

    with pytest.raises(DataProviderError) as excinfo:
        await provider.fetch_candles(_listing(), Timeframe.D1, span)
    assert excinfo.value.code is DataProviderErrorCode.DATA_COVERAGE_MISSING


# ---- negative: 심볼 미존재(포맷 해석 불가, fail-closed) --------------------


async def test_fetch_candles_rejects_unrecognizable_symbol_before_network_call() -> None:
    fake = _FakeKISAdapter({})
    provider = KISProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(days=1))
    bad_listing = _listing(venue_symbol="NOTKRX")

    with pytest.raises(SymbolNormalizationError):
        await provider.fetch_candles(bad_listing, Timeframe.D1, span)
    assert fake.calls == []  # 네트워크 위임 전에 fail-closed


async def test_fetch_candles_rejects_listing_from_other_venue() -> None:
    provider = KISProvider(_FakeKISAdapter({}))
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(days=1))
    bitget_listing = _listing(venue=Venue.BITGET, venue_symbol="BTCUSDT")

    with pytest.raises(ValueError, match="Venue.KIS_KRX"):
        await provider.fetch_candles(bitget_listing, Timeframe.D1, span)


async def test_fetch_candles_rejects_unsupported_timeframe() -> None:
    provider = KISProvider(_FakeKISAdapter({}))
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(days=1))

    with pytest.raises(ValueError, match="KISProvider"):
        await provider.fetch_candles(_listing(), Timeframe.H1, span)


# ---- 미구현 훅: fail-closed(조용한 빈 결과 대신 명시적 실패) --------------


async def test_list_instruments_fails_closed_not_implemented() -> None:
    provider = KISProvider(_FakeKISAdapter({}))
    with pytest.raises(NotImplementedError):
        await provider.list_instruments(AssetClass.KR_EQUITY)


async def test_subscribe_fails_closed_not_implemented() -> None:
    provider = KISProvider(_FakeKISAdapter({}))
    with pytest.raises(NotImplementedError):
        async for _ in provider.subscribe([_listing()]):
            pass
