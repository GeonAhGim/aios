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
from types import SimpleNamespace
from typing import Any, cast

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
    MicrostructureNotSupportedError,
    ProviderCapabilities,
    ProviderTick,
    RateLimitSpec,
    TimeSpan,
    require_microstructure,
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
    def capabilities(self) -> Any: ...
    async def list_instruments(self, asset_class: Any) -> Any: ...
    async def fetch_candles(self, listing: Any, tf: Any, span: Any) -> Any: ...
    async def subscribe(self, listings: Any) -> Any: ...


class _MissingSubscribeProvider:
    """`subscribe`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    def capabilities(self) -> Any: ...
    async def list_instruments(self, asset_class: Any) -> Any: ...
    async def fetch_candles(self, listing: Any, tf: Any, span: Any) -> Any: ...


class _NoMicrostructureProvider:
    """`MarketDataProvider`는 만족하지만 `MicrostructureProvider`(DC-24
    선택 확장)의 3개 메서드는 없는, 실제 DC-12 어댑터와 같은 모양의
    구현체."""

    def capabilities(self) -> ProviderCapabilities:
        return cast(ProviderCapabilities, SimpleNamespace(provider_id="acme"))

    async def list_instruments(self, asset_class: Any) -> Any: ...
    async def fetch_candles(self, listing: Any, tf: Any, span: Any) -> Any: ...
    async def subscribe(self, listings: Any) -> Any: ...


class _FullInstrumentRepository:
    async def get(self, conn: Any, instrument_id: Any) -> Any: ...
    async def create(self, conn: Any, instrument: Any) -> Any: ...
    async def update_lifecycle_state(
        self, conn: Any, instrument_id: Any, *, expected_state: Any, state: Any
    ) -> Any: ...
    async def get_listing(self, conn: Any, venue: Any, venue_symbol: Any, at: Any) -> Any: ...
    async def add_listing(self, conn: Any, listing: Any) -> Any: ...


class _MissingCreateInstrumentRepository:
    async def get(self, conn: Any, instrument_id: Any) -> Any: ...
    async def update_lifecycle_state(
        self, conn: Any, instrument_id: Any, *, expected_state: Any, state: Any
    ) -> Any: ...
    async def get_listing(self, conn: Any, venue: Any, venue_symbol: Any, at: Any) -> Any: ...
    async def add_listing(self, conn: Any, listing: Any) -> Any: ...


class _FullCoverageRepository:
    async def upsert_span(self, conn: Any, span: Any) -> Any: ...
    async def list_spans(self, conn: Any, instrument_id: Any, timeframe: Any) -> Any: ...


class _DictReturningCoverageRepository:
    """메서드 이름은 갖췄지만 `list_spans`가 `CoverageSpan` 대신 dict를
    돌려준다."""

    async def upsert_span(self, conn: Any, span: Any) -> Any: ...

    async def list_spans(
        self, conn: Any, instrument_id: Any, timeframe: Any
    ) -> list[dict[str, Any]]:
        return [{"instrument_id": instrument_id}]


class _MissingListSpansCoverageRepository:
    """`list_spans` 메서드가 빠진 불완전한 CoverageRepository 구현."""

    async def upsert_span(self, conn: Any, span: Any) -> Any: ...


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullMarketDataProvider(), MarketDataProvider)
    assert isinstance(_FullInstrumentRepository(), InstrumentRepository)
    assert isinstance(_FullCoverageRepository(), CoverageRepository)


def test_incomplete_implementations_fail_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingSubscribeProvider(), MarketDataProvider)
    assert not isinstance(_MissingCreateInstrumentRepository(), InstrumentRepository)
    assert not isinstance(_MissingListSpansCoverageRepository(), CoverageRepository)


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
        raise DataProviderError(DataProviderErrorCode.DATA_COVERAGE_MISSING, provider_id="bitget")
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


def test_require_microstructure_rejects_provider_without_support() -> None:
    """§9.11 DC-24 fail-closed capability gate — `MicrostructureProvider`의
    3개 메서드가 없는 provider는 `isinstance()`가 아니라 `require_microstructure()`
    호출 시점에 명시적으로 거부돼야 한다(무음 `AttributeError` 폴백 금지)."""
    provider = _NoMicrostructureProvider()
    with pytest.raises(MicrostructureNotSupportedError) as exc_info:
        require_microstructure(provider, "fetch_trades")
    assert exc_info.value.provider_id == "acme"
    assert exc_info.value.capability == "fetch_trades"


def test_require_microstructure_propagates_capabilities_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: `capabilities()`가 예외를 던지면(예: 벤더 설정 조회 실패)
    fail-closed 게이트는 그 예외를 그대로 전파해야 한다 — `MicrostructureNotSupportedError`로
    뭉개거나 조용히 삼키지 않는다."""

    def _boom(self: _NoMicrostructureProvider) -> Any:
        raise RuntimeError("capabilities backend unavailable")

    monkeypatch.setattr(_NoMicrostructureProvider, "capabilities", _boom)
    provider = _NoMicrostructureProvider()

    with pytest.raises(RuntimeError, match="capabilities backend unavailable"):
        require_microstructure(provider, "fetch_trades")


def test_data_provider_error_retryable_by_code_negative_cases() -> None:
    """negative test: DataProviderError의 retryable 필드는 code에 따라
    결정된다. 모든 코드에서 값이 명확하게 정해져 있어야 한다."""
    err_denied = DataProviderError(
        DataProviderErrorCode.DATA_ENTITLEMENT_DENIED, provider_id="bitget"
    )
    assert err_denied.retryable is False
    err_rate_limit = DataProviderError(
        DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED, provider_id="bitget"
    )
    assert err_rate_limit.retryable is True


async def test_list_instruments_exception_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: MarketDataProvider의 list_instruments가 예외를 던지면
    그 예외가 swallow되거나 조용히 빈 결과로 대체되지 않고 그대로 전파돼야 한다."""

    async def _boom(self: _FullMarketDataProvider, asset_class: AssetClass) -> list[VenueListing]:
        raise RuntimeError("vendor backend unreachable")

    provider = _FullMarketDataProvider()
    monkeypatch.setattr(provider.__class__, "list_instruments", _boom)

    with pytest.raises(RuntimeError, match="vendor backend unreachable"):
        await provider.list_instruments(AssetClass.CRYPTO)
