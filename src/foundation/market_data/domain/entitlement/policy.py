"""DC-9 — pure decision logic for tenant/user data entitlement.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-9, §3.1 (`DATA_ENTITLEMENT_DENIED` in the SPI error taxonomy), §9.2 DC-9.

This module has no knowledge of the entitlement storage schema (the
`entitlements` table, owned by DC-8) — it only decides
`allowed(subject, feed) -> Entitlement` from a list of pure `EntitlementGrant`
DTOs that the caller (application/adapters) has already read and passed in. It
does not build HTTP 403 responses or wire routers (owned by §3.3
EXCEPTION_MAP, task-1179 decision) — on denial it stores the
`DataProviderErrorCode.DATA_ENTITLEMENT_DENIED` value already defined by DC-5
(`ports/provider.py`) as-is in `Entitlement.error_code`, guaranteeing that
value is a structured value mappable to the §3.3 taxonomy's 403 (reused from
a single source instead of redefined here).

Tenant identifiers reuse the concept established by PLT-28
`resolve_tenant_context` (task-1090) as-is — `tenant_id`/`subject_id` are
`UUID`, same as `TenantContext`, and in P0 scope `tenant_id == subject_id`
(personal account). Cross-tenant access is blocked on the same principle as
LA-22 (task-825): "tenant mismatch = deny" is the default — any `grants` the
`subject` brings in whose `tenant_id` differs are excluded from the decision
entirely (never trusted even if they got mixed in via a UUID or cache
corruption; fail-closed).

The decision is a 4-stage funnel (if every candidate is filtered out at a
stage, that stage's reason becomes the denial reason): ① tenant/subject scope
→ ② expiry → ③ venue/asset class/instrument/timeframe scope → ④ realtime
entitlement. If any of stages ①②③ reduces the candidates to zero, it's a
denial (fail-closed — missing entitlement info means deny, not allow). At
stage ④, if realtime was requested but the entitlements matching scope only
allow a delayed feed, it's a partial allow (`mode="delayed"`), not a denial.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, model_validator

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.ports.provider import DataProviderErrorCode

__all__ = [
    "EntitlementGrant",
    "EntitlementSubject",
    "FeedRequest",
    "EntitlementDenialReason",
    "Entitlement",
    "allowed",
]


class EntitlementGrant(BaseModel, frozen=True):
    """Pure representation of a single entitlement record (corresponds to one
    row of the `entitlements` table — this module has no knowledge of that
    table's schema, owned by DC-8)."""

    tenant_id: UUID
    subject_id: UUID | None
    """`None` means the entitlement applies to the whole tenant (all users)."""
    venue: Venue
    asset_class: AssetClass
    instrument_ids: frozenset[str] | None
    """`None` means it applies to all instruments of `venue` x `asset_class`."""
    timeframes: frozenset[Timeframe]
    realtime: bool
    delayed_seconds: int
    expires_at: AwareDatetime | None


class EntitlementSubject(BaseModel, frozen=True):
    """The decision subject (who is asking) plus the entitlements they hold.
    Uses the same `tenant_id`/`subject_id` concept as the `TenantContext`
    issued by `resolve_tenant_context`."""

    tenant_id: UUID
    subject_id: UUID
    grants: tuple[EntitlementGrant, ...]


class FeedRequest(BaseModel, frozen=True):
    """What is being requested (who is asking is already carried by
    `EntitlementSubject`)."""

    venue: Venue
    asset_class: AssetClass
    instrument_id: str
    timeframe: Timeframe
    want_realtime: bool


class EntitlementDenialReason(str, Enum):
    NO_GRANT = "NO_GRANT"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    EXPIRED = "EXPIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Entitlement(BaseModel, frozen=True):
    """The decision result. A `model_validator` enforces that only mutually
    exclusive allow/deny field combinations can be represented (preventing
    the construction of an invalid half-allowed state)."""

    allowed: bool
    mode: Literal["realtime", "delayed"] | None
    delayed_seconds: int | None
    error_code: DataProviderErrorCode | None
    reason: EntitlementDenialReason | None

    @model_validator(mode="after")
    def _allow_deny_are_exclusive(self) -> Entitlement:
        if self.allowed:
            if self.mode is None or self.error_code is not None or self.reason is not None:
                raise ValueError("allowed=True는 mode만 채우고 error_code/reason은 비워야 한다")
            if self.mode == "delayed" and self.delayed_seconds is None:
                raise ValueError("mode='delayed'는 delayed_seconds가 필요하다")
        else:
            if (
                self.mode is not None
                or self.delayed_seconds is not None
                or self.error_code is None
                or self.reason is None
            ):
                raise ValueError("allowed=False는 error_code/reason만 채우고 mode는 비워야 한다")
        return self


def _deny(reason: EntitlementDenialReason) -> Entitlement:
    return Entitlement(
        allowed=False,
        mode=None,
        delayed_seconds=None,
        error_code=DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
        reason=reason,
    )


def _matches_scope(grant: EntitlementGrant, feed: FeedRequest) -> bool:
    if grant.venue != feed.venue or grant.asset_class != feed.asset_class:
        return False
    if grant.instrument_ids is not None and feed.instrument_id not in grant.instrument_ids:
        return False
    return feed.timeframe in grant.timeframes


def allowed(subject: EntitlementSubject, feed: FeedRequest, as_of: datetime) -> Entitlement:
    """Decide whether `subject` may access `feed` as of `as_of`.

    `as_of` is the deterministic clock input used for the expiry check (a
    pure function does not read the current time itself) — the caller passes
    it in as tz-aware UTC.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of는 tz-aware datetime만 받는다")

    tenant_owned = [g for g in subject.grants if g.tenant_id == subject.tenant_id]
    if not tenant_owned:
        if subject.grants:
            return _deny(EntitlementDenialReason.TENANT_MISMATCH)
        return _deny(EntitlementDenialReason.NO_GRANT)

    owned = [g for g in tenant_owned if g.subject_id is None or g.subject_id == subject.subject_id]
    if not owned:
        return _deny(EntitlementDenialReason.NO_GRANT)

    active = [g for g in owned if g.expires_at is None or g.expires_at > as_of]
    if not active:
        return _deny(EntitlementDenialReason.EXPIRED)

    scoped = [g for g in active if _matches_scope(g, feed)]
    if not scoped:
        return _deny(EntitlementDenialReason.OUT_OF_SCOPE)

    if feed.want_realtime and any(g.realtime for g in scoped):
        return Entitlement(
            allowed=True, mode="realtime", delayed_seconds=None, error_code=None, reason=None
        )

    delay = min(g.delayed_seconds for g in scoped)
    return Entitlement(
        allowed=True, mode="delayed", delayed_seconds=delay, error_code=None, reason=None
    )
