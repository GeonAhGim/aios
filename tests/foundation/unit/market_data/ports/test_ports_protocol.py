"""DC-5 `ports/{provider,instrument_repository,coverage_repository}.py` 구조적
계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5, §9.2 DC-5(DoD: "Protocol runtime_checkable 테스트").

`@runtime_checkable` Protocol의 `isinstance()`는 메서드 **이름**만 확인한다
(`tests/unit/market_data/test_ports_protocol.py`, LA-9와 같은 패턴) —
파라미터·반환 타입은 mypy(정적)가 확인한다. negative test는 두 종류다:
(1) 메서드 하나가 빠진 구현은 isinstance()에서부터 False가 되는 fail-closed
사례, (2) 메서드는 다 갖췄지만 DTO 대신 dict를 돌려주는 구현은 isinstance()를
통과해도 그 결과가 계약 DTO 검증은 통과하지 못한다는 사례.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.ports.coverage_repository import (
    CoverageQuality,
    CoverageRepository,
    CoverageSpan,
)
from src.foundation.market_data.ports.instrument_repository import InstrumentRepository
from src.foundation.market_data.ports.provider import (
    DataLineage,
    DataProviderError,
    DataProviderErrorCode,
    MarketDataProvider,
    ProviderCapabilities,
    ProviderTick,
    RateLimitSpec,
    TimeSpan,
)

_IID = "0" * 25 + "1"


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _listing() -> VenueListing:
    return VenueListing(
        instrument_id=_IID,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_now(),
        delisted_at=None,
        is_primary=True,
    )


class _FullMarketDataProvider:
    def capabilities(self): ...
    async def list_instruments(self, asset_class): ...
    async def fetch_candles(self, listing, tf, span): ...
    async def subscribe(self, listings): ...


class _MissingSubscribeProvider:
    """`subscribe`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    def capabilities(self): ...
    async def list_instruments(self, asset_class): ...
    async def fetch_candles(self, listing, tf, span): ...


class _FullInstrumentRepository:
    async def get(self, conn, instrument_id): ...
    async def create(self, conn, instrument): ...
    async def update_lifecycle_state(self, conn, instrument_id, state): ...
    async def get_listing(self, conn, venue, venue_symbol, at): ...
    async def add_listing(self, conn, listing): ...


class _MissingCreateInstrumentRepository:
    async def get(self, conn, instrument_id): ...
    async def update_lifecycle_state(self, conn, instrument_id, state): ...
    async def get_listing(self, conn, venue, venue_symbol, at): ...
    async def add_listing(self, conn, listing): ...


class _FullCoverageRepository:
    async def upsert_span(self, conn, span): ...
    async def list_spans(self, conn, instrument_id, timeframe): ...


class _DictReturningCoverageRepository:
    """메서드 이름은 갖췄지만 `list_spans`가 `CoverageSpan` 대신 dict를
    돌려준다."""

    async def upsert_span(self, conn, span): ...

    async def list_spans(self, conn, instrument_id, timeframe):
        return [{"instrument_id": instrument_id}]


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullMarketDataProvider(), MarketDataProvider)
    assert isinstance(_FullInstrumentRepository(), InstrumentRepository)
    assert isinstance(_FullCoverageRepository(), CoverageRepository)


def test_incomplete_implementations_fail_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingSubscribeProvider(), MarketDataProvider)
    assert not isinstance(_MissingCreateInstrumentRepository(), InstrumentRepository)


async def test_dict_returning_fake_satisfies_isinstance_but_not_the_dto() -> None:
    fake = _DictReturningCoverageRepository()
    assert isinstance(fake, CoverageRepository)

    result = await fake.list_spans(conn=None, instrument_id=_IID, timeframe=Timeframe.M1)
    assert isinstance(result, list)
    with pytest.raises(ValidationError):
        CoverageSpan.model_validate(result[0])


def test_provider_capabilities_round_trip() -> None:
    """§3.1 원문 필드가 그대로 있는지 실제 인스턴스로 증명한다."""
    caps = ProviderCapabilities(
        provider_id="bitget",
        asset_classes=frozenset({AssetClass.CRYPTO}),
        timeframes=frozenset({Timeframe.M1}),
        history_from=None,
        realtime=True,
        delayed_seconds=0,
        max_symbols_per_request=100,
        rate_limit=RateLimitSpec(requests_per_second=Decimal("10"), burst=20),
    )
    assert caps.provider_id == "bitget"
    assert caps.rate_limit.burst == 20


def test_candle_columns_and_time_span_are_reused_as_is() -> None:
    """`fetch_candles`가 §3.1 원문대로 `CandleColumns`를 그대로 쓰는지(새
    타입으로 몰래 갈아치우지 않았는지) 확인한다."""
    span = TimeSpan(start=_now(), end=_now())
    columns = CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])
    assert span.start == span.end
    assert len(columns) == 0


def test_provider_tick_carries_lineage() -> None:
    """§3.1 "lineage(provider_id, fetched_at, raw_digest) 필수" — 이벤트
    단위 스트림(`subscribe`) 결과는 계보를 실어 보낸다."""
    tick = ProviderTick(
        listing=_listing(),
        price=Decimal("50000"),
        quantity=Decimal("0.01"),
        side="buy",
        traded_at=_now(),
        lineage=DataLineage(provider_id="bitget", fetched_at=_now(), raw_digest="deadbeef"),
    )
    assert tick.lineage.provider_id == "bitget"


def test_error_taxonomy_has_exactly_the_four_spec_codes() -> None:
    assert {c.value for c in DataProviderErrorCode} == {
        "DATA_PROVIDER_RATE_LIMITED",
        "DATA_PROVIDER_UNAVAILABLE",
        "DATA_ENTITLEMENT_DENIED",
        "DATA_COVERAGE_MISSING",
    }


@pytest.mark.parametrize(
    "code,expected_retryable",
    [
        (DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED, True),
        (DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, True),
        (DataProviderErrorCode.DATA_ENTITLEMENT_DENIED, False),
        (DataProviderErrorCode.DATA_COVERAGE_MISSING, False),
    ],
)
def test_data_provider_error_retryable_by_code(
    code: DataProviderErrorCode, expected_retryable: bool
) -> None:
    err = DataProviderError(code, provider_id="bitget")
    assert err.retryable is expected_retryable
    assert err.code is code


def test_data_provider_error_coverage_missing_is_not_a_silent_empty_result() -> None:
    """§4.1 "조용한 0 채움 금지" — 커버리지 없음은 예외지, 빈 리스트가
    아니다."""
    with pytest.raises(DataProviderError) as exc_info:
        raise DataProviderError(
            DataProviderErrorCode.DATA_COVERAGE_MISSING, provider_id="bitget"
        )
    assert exc_info.value.code is DataProviderErrorCode.DATA_COVERAGE_MISSING
    assert exc_info.value.retryable is False


def test_instrument_and_venue_listing_from_dc1_are_reused_unchanged() -> None:
    """DC-1 계약을 임의로 확장하지 않았는지 실제 인스턴스로 확인한다."""
    instrument = Instrument(
        instrument_id=_IID,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=InstrumentLifecycle.ACTIVE,
        created_at=_now(),
    )
    listing = _listing()
    assert instrument.instrument_id == listing.instrument_id


def test_coverage_span_quality_enum() -> None:
    span = CoverageSpan(
        instrument_id=_IID,
        venue=Venue.BITGET,
        timeframe=Timeframe.M1,
        quality=CoverageQuality.VALIDATED,
        start=_now(),
        end=_now(),
    )
    assert span.quality is CoverageQuality.VALIDATED
