"""DC-24 `ports/provider.py` 확장 — 마이크로구조 선택 capability
(`fetch_trades`/`fetch_quotes`/`subscribe_book`) 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.11 DC-24 (DoD: "capability 미지원 시 명시적 오류(무음 폴백 금지)").

`MicrostructureProvider`는 `MarketDataProvider`와 분리된 별도 Protocol이다
(기존 DC-12 어댑터가 이 3메서드를 강제로 갖추지 않아도 되게). 미지원 신호는
`DataProviderErrorCode`에 5번째 멤버를 더하는 대신 전용 예외
`MicrostructureNotSupportedError`로 낸다 — `adapters/providers/
base_adapter.py::NormalizationNotImplementedError`와 같은 전례.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.contracts.v2.microstructure import (
    Aggressor,
    QuoteL1,
    TradeTick,
)
from src.foundation.market_data.ports.provider import (
    MicrostructureNotSupportedError,
    MicrostructureProvider,
    ProviderCapabilities,
    RateLimitSpec,
    TimeSpan,
    require_microstructure,
)

_IID = "0" * 25 + "1"
_TS_EVENT = 1_700_000_000_000_000_000
_TS_RECV = _TS_EVENT + 1_000_000


def _now() -> datetime:
    return datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def _listing() -> VenueListing:
    return VenueListing(
        instrument_id=_IID,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_now(),
        delisted_at=None,
        is_primary=True,
    )


def _caps(provider_id: str) -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_id=provider_id,
        asset_classes=frozenset({AssetClass.CRYPTO}),
        timeframes=frozenset({Timeframe.M1}),
        history_from=None,
        realtime=True,
        delayed_seconds=0,
        max_symbols_per_request=100,
        rate_limit=RateLimitSpec(requests_per_second=Decimal("10"), burst=20),
    )


class _CandleOnlyProvider:
    """DC-12 어댑터처럼 캔들만 다루는, 마이크로구조 미지원 provider."""

    def capabilities(self) -> ProviderCapabilities:
        return _caps("bitget")

    async def list_instruments(self, asset_class): ...

    async def fetch_candles(self, listing, tf, span): ...

    async def subscribe(self, listings): ...


class _MicrostructureCapableProvider(_CandleOnlyProvider):
    """마이크로구조 3메서드를 모두 갖춘 provider."""

    async def fetch_trades(self, listing, span):
        return [
            TradeTick(
                instrument_id=_IID,
                venue=Venue.BITGET,
                ts_event=_TS_EVENT,
                ts_recv=_TS_RECV,
                seq=1,
                price=Decimal("50000"),
                size=Decimal("0.01"),
                aggressor=Aggressor.BUY,
            )
        ]

    async def fetch_quotes(self, listing, span):
        return [
            QuoteL1(
                instrument_id=_IID,
                venue=Venue.BITGET,
                ts_event=_TS_EVENT,
                ts_recv=_TS_RECV,
                seq=1,
                bid_price=Decimal("49999"),
                bid_size=Decimal("1"),
                ask_price=Decimal("50001"),
                ask_size=Decimal("1"),
            )
        ]

    async def subscribe_book(self, listings):
        if False:
            yield  # pragma: no cover - makes this an async generator


class _MissingSubscribeBookProvider(_CandleOnlyProvider):
    """`fetch_trades`/`fetch_quotes`는 갖췄지만 `subscribe_book`만 빠진
    불완전 구현 — Protocol을 만족하지 못해야 한다(negative test 1)."""

    async def fetch_trades(self, listing, span): ...

    async def fetch_quotes(self, listing, span): ...


def test_candle_only_provider_does_not_satisfy_microstructure_protocol() -> None:
    """미지원 provider는 `isinstance()`에서부터 False다(negative test 2)."""
    assert not isinstance(_CandleOnlyProvider(), MicrostructureProvider)


def test_missing_one_method_fails_the_protocol_check() -> None:
    assert not isinstance(_MissingSubscribeBookProvider(), MicrostructureProvider)


def test_capable_provider_satisfies_microstructure_protocol() -> None:
    assert isinstance(_MicrostructureCapableProvider(), MicrostructureProvider)


def test_require_microstructure_returns_the_same_provider_when_supported() -> None:
    provider = _MicrostructureCapableProvider()
    assert require_microstructure(provider, "fetch_trades") is provider


def test_require_microstructure_raises_dedicated_exception_when_unsupported() -> None:
    """DoD: 무음 폴백 금지 — 미지원 provider는 명시적 예외를 낸다(negative
    test 3, failure-injection: 호출 시점에 실패를 강제로 유발한다)."""
    provider = _CandleOnlyProvider()
    with pytest.raises(MicrostructureNotSupportedError) as exc_info:
        require_microstructure(provider, "fetch_trades")
    assert exc_info.value.provider_id == "bitget"
    assert exc_info.value.capability == "fetch_trades"


def test_require_microstructure_error_is_not_a_data_provider_error_code() -> None:
    """미지원 신호가 `DataProviderErrorCode`의 5번째 멤버가 아니라 전용
    예외 클래스임을 증명한다(§C 중복 컨텍스트 금지 확인)."""
    assert not hasattr(MicrostructureNotSupportedError, "code")
    assert issubclass(MicrostructureNotSupportedError, NotImplementedError)


async def test_fetch_trades_and_fetch_quotes_reuse_dc19_dtos_as_is() -> None:
    """provider.py 안에 Trade/Quote DTO를 새로 정의하지 않고 DC-19
    `contracts/v2/microstructure.py`를 그대로 재사용하는지 확인한다."""
    provider = require_microstructure(_MicrostructureCapableProvider(), "fetch_trades")
    span = TimeSpan(start=_now(), end=_now())
    trades = await provider.fetch_trades(_listing(), span)
    quotes = await provider.fetch_quotes(_listing(), span)
    assert isinstance(trades[0], TradeTick)
    assert isinstance(quotes[0], QuoteL1)


async def test_subscribe_book_missing_yields_nothing_not_none() -> None:
    provider = require_microstructure(_MicrostructureCapableProvider(), "subscribe_book")
    books = [b async for b in provider.subscribe_book([_listing()])]
    assert books == []


def test_require_microstructure_check_is_fast_at_scale() -> None:
    """성능 어서션(D2 필수) — capability 게이트는 캔들/틱 핫 경로에서
    호출당 마이크로초 단위여야 하므로, 10,000회 반복이 200ms(p_all)를
    넘지 않아야 한다(단순 isinstance() 체크이므로 여유 있는 예산)."""
    provider = _MicrostructureCapableProvider()
    iterations = 10_000
    started = time.perf_counter()
    for _ in range(iterations):
        require_microstructure(provider, "fetch_trades")
    elapsed = time.perf_counter() - started
    assert elapsed < 0.2, f"require_microstructure too slow: {elapsed:.4f}s for {iterations} calls"
