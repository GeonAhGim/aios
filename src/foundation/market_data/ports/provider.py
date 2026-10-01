"""DC-5 — `MarketDataProvider` SPI (vendor-neutral market data provider port) +
error taxonomy.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5, §3.1 (SPI contract source), §4.1 (fail-closed), §9.2 DC-5.

Carries over the §3.1 source signature verbatim — DC-11 (`adapters/providers/
base_adapter.py`) and DC-12 (per-exchange adapters) depend on this Protocol
1:1, so no methods are added or changed (task-1126 decision). Even though its
purpose looks like it overlaps `ports/ingest_source.py` (LA-9), the two are not
merged — `IngestSource` only handles raw per-exchange candle fetches, while
this SPI covers the new provider-neutral axis (capabilities, entitlement, and
subscribe).

All returns are UTC tz-aware and `Decimal`, and must carry lineage
(`DataLineage`: provider_id, fetched_at, raw_digest) (§3.1). Per the §3.1
source, `fetch_candles` returns batch-level `CandleColumns`
(ADR-2026-09-04-A) as-is, so the `DataLineage` for the whole batch is not
part of this return value — the caller records it separately at persistence
time (same spot as LA-8 `domain/lineage.py` / LA-9 `BatchRepository`).
`subscribe()` is an event-level stream, so it carries a `DataLineage` on each
event (`ProviderTick` / `ProviderCandle`).

No silent zero-filling (§4.1) — a span outside coverage raises
`DataProviderError(DATA_COVERAGE_MISSING)`, and an unentitled feed raises
`DATA_ENTITLEMENT_DENIED` (never substituted with an empty list).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel

from src.core.exceptions import MihwaError
from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.contracts.v2.microstructure import (
    BookL2,
    QuoteL1,
    TradeTick,
)
from src.foundation.market_data.domain.candle_columns import CandleColumns


class RateLimitSpec(BaseModel):
    """§3.1 `rate_limit`. Holds only the declared token-bucket parameters —
    actual rate limiting enforcement belongs to DC-11 `base_adapter.py`."""

    requests_per_second: Decimal
    burst: int


class ProviderCapabilities(BaseModel):
    """Verbatim from the §3.1 source. Do not change field order or names
    (DC-11/12 depend on it)."""

    provider_id: str
    asset_classes: frozenset[AssetClass]
    timeframes: frozenset[Timeframe]
    history_from: AwareDatetime | None
    realtime: bool
    delayed_seconds: int
    max_symbols_per_request: int
    rate_limit: RateLimitSpec


class TimeSpan(BaseModel):
    """The query span `[start, end)` for `fetch_candles`. §3.1 only mentions
    the name without defining it in the body, so this follows the
    `[start, end)` convention of LA-9 `IngestSource.fetch_candles` as-is."""

    start: AwareDatetime
    end: AwareDatetime


class DataLineage(BaseModel):
    """§3.1 "lineage(provider_id, fetched_at, raw_digest) is mandatory"."""

    provider_id: str
    fetched_at: AwareDatetime
    raw_digest: str


class ProviderTick(BaseModel):
    listing: VenueListing
    price: Decimal
    quantity: Decimal
    side: Literal["buy", "sell"]
    traded_at: AwareDatetime
    lineage: DataLineage


class ProviderCandle(BaseModel):
    listing: VenueListing
    tf: Timeframe
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    lineage: DataLineage


TickOrCandle = ProviderTick | ProviderCandle


@runtime_checkable
class MarketDataProvider(Protocol):
    """Verbatim from §3.1. `domain/`/`application/` know only this Protocol
    and are unaware of the actual vendor implementation (DC-12) (ref #71 §4)."""

    def capabilities(self) -> ProviderCapabilities: ...

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        """The venue symbol list this provider handles. An empty list for an
        unsupported asset class (not an error) — a lookup failure itself is
        raised as an exception."""
        ...

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        """`[span.start, span.end)`. If the provider cannot cover that span,
        raise `DataProviderError(DATA_COVERAGE_MISSING)` (§4.1, no 0/NaN
        filling)."""
        ...

    async def subscribe(self, listings: Sequence[VenueListing]) -> AsyncIterator[TickOrCandle]:
        """Realtime/delayed stream. If `capabilities().realtime=False`, sends
        only the delayed feed (`delayed_seconds`)."""
        ...


class MicrostructureNotSupportedError(NotImplementedError):
    """§9.11 DC-24 — a provider does not implement tick-level microstructure
    capabilities (`fetch_trades`/`fetch_quotes`/`subscribe_book`). This is a
    dedicated exception, not a 5th `DataProviderErrorCode` member: that enum's
    docstring pins the taxonomy to exactly its 4 codes (a capability gap is
    not one of §3.1's 4 failure modes). Same precedent as
    `adapters/providers/base_adapter.py::NormalizationNotImplementedError` —
    fail closed with a named exception instead of a silent fallback."""

    def __init__(self, provider_id: str, capability: str) -> None:
        self.provider_id = provider_id
        self.capability = capability
        super().__init__(
            f"provider {provider_id!r} does not support optional microstructure "
            f"capability {capability!r} (§9.11 DC-24)"
        )


@runtime_checkable
class MicrostructureProvider(Protocol):
    """§9.11 DC-24 — optional microstructure capability, kept **separate**
    from `MarketDataProvider` so existing DC-12 adapters (Bitget/KIS) are not
    forced to grow these 3 methods. A provider that supports tick-level data
    implements this Protocol in addition to `MarketDataProvider`; callers use
    `require_microstructure()` (or `isinstance()`) to fail closed instead of
    hitting a silent `AttributeError`/empty-result fallback when the vendor
    lacks the feed."""

    async def fetch_trades(self, listing: VenueListing, span: TimeSpan) -> Sequence[TradeTick]: ...

    async def fetch_quotes(self, listing: VenueListing, span: TimeSpan) -> Sequence[QuoteL1]: ...

    async def subscribe_book(self, listings: Sequence[VenueListing]) -> AsyncIterator[BookL2]: ...


def require_microstructure(provider: MarketDataProvider, capability: str) -> MicrostructureProvider:
    """Fail-closed capability gate (§9.11 DC-24 DoD: "an unsupported
    capability must raise an explicit error, no silent fallback"). Call this before invoking
    `fetch_trades`/`fetch_quotes`/`subscribe_book` on a plain
    `MarketDataProvider` reference."""

    if not isinstance(provider, MicrostructureProvider):
        raise MicrostructureNotSupportedError(provider.capabilities().provider_id, capability)
    return provider


class DataProviderErrorCode(str, Enum):
    """§3.1 error taxonomy. A failure outside these 4 codes must be
    propagated by the caller as the original exception — an unknown failure
    is never forced into this list (same principle as §4.1 fail-closed, in
    contrast to `ExchangeErrorKind.UNKNOWN_RESPONSE`: here, "if unknown"
    does not mean "lump it into this code," it means "do not use this
    taxonomy at all")."""

    DATA_PROVIDER_RATE_LIMITED = "DATA_PROVIDER_RATE_LIMITED"
    DATA_PROVIDER_UNAVAILABLE = "DATA_PROVIDER_UNAVAILABLE"
    DATA_ENTITLEMENT_DENIED = "DATA_ENTITLEMENT_DENIED"
    DATA_COVERAGE_MISSING = "DATA_COVERAGE_MISSING"


_RETRYABLE_CODES = frozenset(
    {
        DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED,
        DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE,
    }
)


class DataProviderError(MihwaError):
    """Common representation of the §3.1 error 4-way split. `retryable` is
    determined by `code` and is not overridden by the caller (the retry
    policy itself belongs to DC-11 `base_adapter.py`; this only
    classifies)."""

    def __init__(
        self,
        code: DataProviderErrorCode,
        *,
        provider_id: str,
        retry_after_sec: float | None = None,
        message: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = code in _RETRYABLE_CODES
        self.provider_id = provider_id
        self.retry_after_sec = retry_after_sec
        super().__init__(message or f"데이터 공급 오류: code={code.value} provider={provider_id}")
