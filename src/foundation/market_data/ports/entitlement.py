"""LA-24 — Data entitlement adjudication port + PAPER default implementation.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

The port contract is a single `allowed(subject, feed) -> Entitlement` method. The
adjudication result, subject, and feed type reuse the types already defined by
DC-9 (`domain/entitlement/policy.py`, task-1179): `Entitlement`/`EntitlementSubject`/`FeedRequest`
— this leaf does not create new types, following the spec sentence that
"DC-9 replaces with a policy implementation for the same port (no duplicate definitions)".
The router (`src/api/routers/market_data.py`) knows only this Protocol;
`src/api/foundation_deps.py` decides which implementation is plugged in.

The default implementation `PaperTenantVenueEntitlement` implements the spec sentence
"only ventures registered by the tenant itself; PAPER gets delayed=0":

* "Registered venture" = the set of `venue` values from non-expired rows in the
  `entitlements` table (DC-8 migration 9049e2b6b0b7) belonging to that tenant.
  I/O to read this set is delegated to `VenueRegistrySource`
  (adapters/postgres_tenant_venues.py); this module holds no SQL.
* When allowed, always returns `mode="delayed", delayed_seconds=0` (PAPER scope
  has no distinction between real-time feeds). It does not perform fine-grained
  adjudication per timeframe/feed_type/subject — that is the responsibility of
  the DC-9 policy implementation that will replace this.
* When no registered venture matches, denies with `NO_GRANT` (fail-closed —
  missing information is a denial, not an allowance). Registrations from other
  tenants are not even queried, so `TENANT_MISMATCH` never occurs in this
  implementation.

**Unverified**: Interpreting "registered venture" as rows in the `entitlements`
table is a decision of this leaf (the spec §9 row does not specify a storage
source). The DC-9 policy implementation reads the same table more granularly,
so the source will match.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.entitlement.policy import (
    Entitlement,
    EntitlementDenialReason,
    EntitlementSubject,
    FeedRequest,
)
from src.foundation.market_data.ports.provider import DataProviderErrorCode

__all__ = [
    "EntitlementPort",
    "PaperTenantVenueEntitlement",
    "VenueRegistrySource",
]


@runtime_checkable
class EntitlementPort(Protocol):
    async def allowed(self, subject: EntitlementSubject, feed: FeedRequest) -> Entitlement:
        """Adjudicate whether `subject` may read from `feed`. Denial returns
        `Entitlement(allowed=False, ...)` rather than raising — HTTP status code
        translation (404-equivalent for other tenants) is the router's concern."""
        ...


@runtime_checkable
class VenueRegistrySource(Protocol):
    async def registered_venues(self, tenant_id: UUID) -> frozenset[Venue]:
        """Set of ventures registered by ``tenant_id`` (holding non-expired
        entitlements). Empty set if none."""
        ...


class PaperTenantVenueEntitlement:
    """Default implementation — follows the rules in the module docstring.
    Pure adjudication + one injected I/O call."""

    def __init__(self, source: VenueRegistrySource) -> None:
        self._source = source

    async def allowed(self, subject: EntitlementSubject, feed: FeedRequest) -> Entitlement:
        venues = await self._source.registered_venues(subject.tenant_id)
        if feed.venue not in venues:
            return Entitlement(
                allowed=False,
                mode=None,
                delayed_seconds=None,
                error_code=DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
                reason=EntitlementDenialReason.NO_GRANT,
            )
        return Entitlement(
            allowed=True, mode="delayed", delayed_seconds=0, error_code=None, reason=None
        )
