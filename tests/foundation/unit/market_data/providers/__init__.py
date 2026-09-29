"""task-8404: DC-12 provider entry-point fail-closed regression tests.

Run this module explicitly; pytest's default test_*.py discovery excludes it.
INVARIANTS I-07/I-10: exercise the real provider gate before adapter I/O.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.foundation.market_data.adapters.providers.bitget_provider import BitgetProvider
from src.foundation.market_data.adapters.providers.kis_provider import KISProvider
from src.foundation.market_data.adapters.providers.nh_provider import NHProvider
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    TimeSpan,
)

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PROVIDERS = [
    (BitgetProvider, Venue.BITGET, "BTCUSDT", "get_history_candles"),
    (KISProvider, Venue.KIS_KRX, "005930", "get_ohlcv"),
    (NHProvider, Venue.NH_KRX, "005930", "get_ohlcv"),
]


def _listing(venue: Venue, symbol: str) -> VenueListing:
    return VenueListing(
        instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        venue=venue,
        venue_symbol=symbol,
        listed_at=_BASE - timedelta(days=1),
        delisted_at=None,
        is_primary=True,
    )


def _span() -> TimeSpan:
    return TimeSpan(start=_BASE, end=_BASE + timedelta(days=1))


@pytest.mark.parametrize("provider_type,venue,symbol,method", _PROVIDERS)
async def test_negative_foreign_venue_rejected_before_adapter_io(
    provider_type, venue, symbol, method
) -> None:
    adapter = AsyncMock()
    foreign_venue = Venue.KIS_KRX if venue is Venue.BITGET else Venue.BITGET
    with pytest.raises(ValueError, match="listing"):
        await provider_type(adapter).fetch_candles(
            _listing(foreign_venue, symbol), Timeframe.D1, _span()
        )
    getattr(adapter, method).assert_not_called()


@pytest.mark.parametrize("provider_type,venue,symbol,method", _PROVIDERS)
async def test_negative_empty_coverage_is_not_success_or_retried(
    provider_type, venue, symbol, method
) -> None:
    adapter = AsyncMock()
    fetch = getattr(adapter, method)
    fetch.return_value = []
    provider = provider_type(adapter)
    with pytest.raises(DataProviderError) as caught:
        await provider.fetch_candles(_listing(venue, symbol), Timeframe.D1, _span())
    assert caught.value.code is DataProviderErrorCode.DATA_COVERAGE_MISSING
    assert caught.value.provider_id == provider.capabilities().provider_id
    fetch.assert_awaited_once()


@pytest.mark.parametrize("provider_type,venue,symbol,method", _PROVIDERS)
async def test_failure_injection_entitlement_denial_preserves_error_and_never_retries(
    provider_type, venue, symbol, method, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = AsyncMock()
    provider = provider_type(adapter)
    failure = DataProviderError(
        DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
        provider_id=provider.capabilities().provider_id,
    )
    fetch = AsyncMock(side_effect=failure)
    monkeypatch.setattr(adapter, method, fetch)
    with pytest.raises(DataProviderError) as caught:
        await provider.fetch_candles(_listing(venue, symbol), Timeframe.D1, _span())
    assert caught.value is failure
    fetch.assert_awaited_once()
