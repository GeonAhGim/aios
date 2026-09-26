"""FA-1 — Entity hierarchy (LegalEntity/Fund/Portfolio/SubAccount) contract v1.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 (table in §2.1, §4 FA-A1).

The sole public surface for the 4-level `LegalEntity → Fund → Portfolio → SubAccount`
hierarchy. `domain/` (hierarchy.py, defaults.py) imports this file, but this file
does not import `domain/` (task 71 §4, same principle as LC-1/LB-1). Adding a
field is minor (task 107, a default is required) — removal or a meaning change
requires a new `v2` module.

There are no Decimal money fields (pure identity/hierarchy information) — the
LC-1/LB-1/DC-1 convention of "serialize Decimal as a string" does not apply here.
`closed_at` is not in the §2.1 table, but it is a state required to enforce the
§4 "closure rules" (hierarchy.py), so it was added to all four types following
the `closed_at: datetime | None` convention from the positions module
(legacy_positions_projection.py) as-is — `None` means active (open); a value
means it was closed at that timestamp, and no new child entity can be attached
afterward (FA_HIERARCHY_VIOLATION).
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from src.data.models.base import Currency

SCHEMA_VERSION: Literal["v1"] = "v1"


class EntityErrorCode(str, Enum):
    """Hierarchy-related codes that this leaf (FA-1) defines out of the §3 error
    taxonomy. The actual exception classes are owned by `domain/hierarchy.py`;
    this contract only defines the code values (same principle as LB-1's
    `PositionErrorCode`)."""

    HIERARCHY_VIOLATION = "FA_HIERARCHY_VIOLATION"  # 400, no parent / closed parent / cycle
    ALREADY_CLOSED = "FA_ALREADY_CLOSED"  # 409, attempt to re-close an already-closed entity
    CLOSE_BLOCKED_BY_CHILD = "FA_CLOSE_BLOCKED_BY_CHILD"  # 409, active child remains, close blocked
    REGION_DENIED = (
        "FA_REGION_DENIED"  # 403, write targets a region the entity's region_tag disallows
    )


class LegalEntity(BaseModel):
    """§2.1: The top of the multi-entity axis. `region_tag` is the raw tag that
    FA-24 (data sovereignty) reuses to determine storage location; this leaf
    only holds the value."""

    entity_id: UUID
    tenant_id: UUID
    name: str
    jurisdiction: str
    region_tag: str
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class Fund(BaseModel):
    """§2.1: `base_currency` is inherited by every Portfolio/SubAccount under
    this fund (hierarchy.py `resolve_*_currency`) — Portfolio/SubAccount do
    not have their own currency field. `mandate_ref` is a mandate id from the
    `mandates` bounded context — it is Optional because a mandate may not
    exist yet at fund-creation time (draft state); when `None`, risk_gate
    treats no mandate as bound to this fund yet."""

    fund_id: UUID
    entity_id: UUID
    base_currency: Currency
    mandate_ref: UUID | None = None
    inception: date
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class Portfolio(BaseModel):
    """§2.1: `venue_account_ref` is the account identifier string on the
    exchange/broker side (not validated, since the format differs per vendor —
    unlike LC-1's AccountCode, this value is an opaque string issued by an
    external system)."""

    portfolio_id: UUID
    fund_id: UUID
    venue_account_ref: str
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SubAccount(BaseModel):
    """§2.1: The allocation target unit for block allocation (FA-7/8).
    `owner_ref` is an id pointing to the beneficiary of this sub-account (an
    individual user or a separate client) — it uses the same UUID space as an
    auth subject id, but this contract does not enforce that scope (it is a
    different bounded context the domain layer doesn't know about, so only the
    reference is kept)."""

    sub_account_id: UUID
    portfolio_id: UUID
    owner_ref: UUID
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class EntityContext(BaseModel):
    """FA-5: The sole return type of `application/resolve_context.py` — the
    "resolved" context shared by order/position/ledger write entry points.
    All five fields are required (there is no partially-resolved state) — if
    even one field fails to resolve, this type is never constructed and
    `EntityContextResolutionError` is raised instead (fail-closed, no value
    guessing or default fallback)."""

    tenant_id: UUID
    legal_entity_id: UUID
    fund_id: UUID
    portfolio_id: UUID
    sub_account_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION
