"""DC-26 — tenant-scoped option-chain read endpoint.

The persistence/query implementation is a port because DC-20's derivative
reference persistence is not part of this leaf.  The default source is
fail-closed and empty until that adapter is installed.  No Greek calculation
is performed here; IND owns that boundary (see ``greek_calculator.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.pagination import PageMeta
from src.api.deps import get_pool
from src.api.foundation_deps import (
    get_market_reference_reader,
    get_market_reference_repository,
    get_tenant_context,
    get_venue_registry_source,
)
from src.api.schemas.options_chain import OptionChainView, OptionContractView
from src.foundation.market_data.application.read_api import authorize_venue, resolve_instrument
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.ports.entitlement import VenueRegistrySource
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/foundation/market-data", tags=["foundation:market-data"])
_PAGE_MAX = 500


class OptionChainSource(Protocol):
    async def list_chain(
        self,
        *,
        tenant_id: UUID,
        underlying_id: UUID,
        expiry: AwareDatetime | None,
        limit: int,
    ) -> list[OptionContractView]:
        """Return only contracts visible to ``tenant_id``."""
        ...


class EmptyOptionChainSource:
    """Default until DC-20 derivative persistence supplies a real adapter."""

    async def list_chain(
        self,
        *,
        tenant_id: UUID,
        underlying_id: UUID,
        expiry: AwareDatetime | None,
        limit: int,
    ) -> list[OptionContractView]:
        del tenant_id, underlying_id, expiry, limit
        return []


def get_option_chain_source() -> OptionChainSource:
    return EmptyOptionChainSource()


@router.get("/options/chain")
async def get_options_chain(
    venue: Venue,
    symbol: str | None = None,
    instrument_id: UUID | None = None,
    expiry: AwareDatetime | None = None,
    limit: int = Query(100, ge=1, le=_PAGE_MAX),
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    refs: ReferenceRepository = Depends(get_market_reference_repository),
    reader: ReferenceReadRepository = Depends(get_market_reference_reader),
    venues: VenueRegistrySource = Depends(get_venue_registry_source),
    source: OptionChainSource = Depends(get_option_chain_source),
) -> ApiResponse[OptionChainView]:
    """Return a tenant-authorized chain; a foreign underlying is indistinguishable from missing.

    The read/write reference port resolves symbols; the read-only port resolves
    UUIDs. Both are injected so the existing tenant-safe seams remain usable.
    """
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        underlying = await resolve_instrument(
            conn,
            refs=refs,
            reader=reader,
            venue=venue,
            symbol=symbol,
            instrument_id=instrument_id,
            now=now,
        )
    await authorize_venue(venues, tenant_id=context.tenant_id, inst=underlying)
    contracts = await source.list_chain(
        tenant_id=context.tenant_id,
        underlying_id=underlying.instrument_id,
        expiry=expiry,
        limit=limit,
    )
    return ok(
        OptionChainView(
            underlying_id=underlying.instrument_id,
            underlying_symbol=underlying.canonical_symbol,
            as_of=now,
            contracts=contracts,
        ),
        page=PageMeta(size=limit, next_cursor=None),
    )


__all__ = ["EmptyOptionChainSource", "OptionChainSource", "get_option_chain_source", "router"]
