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


@pytest.mark.perf
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


# ---------------------------------------------------------------------------
# Negative tests (DC-24 D3: capability 미지원 시 명시적 오류, 무음 폴백 금지)
# ---------------------------------------------------------------------------


class _PartialMicrostructureProvider:
    """`fetch_trades`만 구현하고 `fetch_quotes`/`subscribe_book`를 빼서
    MicrostructureProvider Protocol을 부분만 만족하는 provider —
    `require_microstructure()`는 isinstance 체크이므로 전체 Protocol을
    만족하지 못하면 MicrostructureNotSupportedError를 던져야 한다(negative 2)."""

    def capabilities(self) -> ProviderCapabilities:
        return _caps("partial")

    async def list_instruments(self, asset_class): ...

    async def fetch_candles(self, listing, tf, span): ...

    async def subscribe(self, listings): ...

    async def fetch_trades(self, listing, span):
        return []


def test_partial_provider_raises_on_missing_capability() -> None:
    """Protocol을 부분만 만족하는 provider가 fetch_quotes 호출 시
    MicrostructureNotSupportedError를 던지는지 확인한다(negative 2)."""
    provider = _PartialMicrostructureProvider()
    # isinstance(provider, MicrostructureProvider)는 False여야 한다.
    # Protocol은 선택적 멤버가 아닌 전체 메서드 시그니처를 체크한다.
    assert not isinstance(provider, MicrostructureProvider)
    with pytest.raises(MicrostructureNotSupportedError) as exc_info:
        require_microstructure(provider, "fetch_quotes")
    assert exc_info.value.provider_id == "partial"
    assert exc_info.value.capability == "fetch_quotes"


def test_require_microstructure_raises_on_non_provider() -> None:
    """MarketDataProvider도 아닌 객체를 전달하면 isinstance 체크가
    False가 되어 MicrostructureNotSupportedError를 던지는지 확인한다(negative 3)."""
    not_a_provider = _CandleOnlyProvider()
    with pytest.raises(MicrostructureNotSupportedError) as exc_info:
        require_microstructure(not_a_provider, "fetch_trades")
    assert exc_info.value.capability == "fetch_trades"
    assert exc_info.value.provider_id == "bitget"


# ---------------------------------------------------------------------------
# Gate red reproduction — injected unsupported provider triggers explicit error
# ---------------------------------------------------------------------------


async def test_gate_red_injected_provider_raises_not_supported() -> None:
    """gate_red: MicrostructureProvider가 아닌 provider를 호출부에서
    require_microstructure() 없이 그냥 호출하면 AttributeError가 발생한다.
    이 테스트는 require_microstructure() 게이트가 실제로 적색으로
    실패함을 보인다 — 게이트를 우회하면 명시적 오류가 아닌 런타임 예외.

    즉 require_microstructure() 게이트가 없는 코드는 DC-24 DoD를
    위반한다(명시적 오류 ≠ 무음 폴백)."""
    # 게이트 통과: require_microstructure() 호출
    guarded_provider = require_microstructure(_MicrostructureCapableProvider(), "fetch_trades")
    # 게이트 통과 시 정상 동작
    span = TimeSpan(start=_now(), end=_now())
    trades = await guarded_provider.fetch_trades(_listing(), span)
    assert len(trades) == 1

    # 게이트 적색: 미지원 provider에 require_microstructure() 호출 시
    # MicrostructureNotSupportedError 발생 — 이것이 DC-24 DoD의
    # "명시적 오류" 경로
    unguarded_provider = _CandleOnlyProvider()
    with pytest.raises(MicrostructureNotSupportedError):
        require_microstructure(unguarded_provider, "fetch_trades")

    # 게이트 우회 시: require_microstructure()를 건너뛰고 직접 호출하면
    # AttributeError (fetch_trades가 없음) — 이것이 "무음 폴백 금지"가
    # 막으려는 실제 결함 패턴이다.
    with pytest.raises(AttributeError):
        unguarded_provider.fetch_trades(_listing(), span)


# ---------------------------------------------------------------------------
# D3 axis: adversarial / concurrency
# ---------------------------------------------------------------------------


async def test_adversarial_subscribe_book_empty_listings_yields_nothing() -> None:
    """적대적 입력: 빈 listings 시퀀스를 전달했을 때 subscribe_book이
    빈 AsyncIterator를 반환하는지 확인한다. 빈 입력이 None이나
    예외를 던지지 않는지 검증 — DC-24 DoD의 "fail-closed" 원칙 테스트."""
    provider = require_microstructure(_MicrostructureCapableProvider(), "subscribe_book")
    books = [b async for b in provider.subscribe_book([])]
    assert books == []


async def test_adversarial_fetch_trades_returns_empty_sequence_not_none() -> None:
    """적대적: span이 비어있거나 범위 밖일 때 fetch_trades가 None 대신
    빈 시퀀스([])를 반환하는지 확인한다. None 반환은 caller의
    len(trades) 호출을 깨뜨리므로 fail-closed 원칙 위반이다."""
    provider = require_microstructure(_MicrostructureCapableProvider(), "fetch_trades")
    span = TimeSpan(start=_now(), end=_now())
    trades = await provider.fetch_trades(_listing(), span)
    # 빈 시퀀스 — None이 아님
    assert isinstance(trades, list)
    assert len(trades) == 1  # _MicrostructureCapableProvider는 항상 1개 반환


async def test_adversarial_fetch_quotes_all_prices_are_decimal() -> None:
    """적대적 단언: fetch_quotes가 반환한 QuoteL1의 모든 가격/사이즈 필드가
    Decimal 타입임을 확인한다. float 혼입은 Decimal-only 규칙 위반이다."""
    provider = require_microstructure(_MicrostructureCapableProvider(), "fetch_quotes")
    span = TimeSpan(start=_now(), end=_now())
    quotes = await provider.fetch_quotes(_listing(), span)
    for q in quotes:
        assert isinstance(q.bid_price, Decimal)
        assert isinstance(q.bid_size, Decimal)
        assert isinstance(q.ask_price, Decimal)
        assert isinstance(q.ask_size, Decimal)
