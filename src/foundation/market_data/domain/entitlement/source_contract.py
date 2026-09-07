"""DC-27 — source_contract: source contract tier / redistribution scope
(pure determination).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1/D2/D7. Extends `entitlements` (9049e2b6b0b7:100,
`domain/entitlement/policy.py` DC-9) and the keyring-handle pattern
(`src/foundation/connections/domain/models.py`
`CredentialBinding.vault_secret_ref`) — no new context is created (D1 "not
a new context").

Where `entitlements` answers "can this tenant see this venue", `source_
contract` answers "at what tier, and how far, are we allowed to expose this
source" — the subject is not the tenant but a single per-source contract
the platform has entered into (D1 "replace tier with a row"). Adapters
(e.g. a future OPENDART/ECOS ingest_source, D6 "not registered before a
contract exists") only know the `source_id` string and know nothing about
this row — `authorize_source()` is the sole determination function the
pre-call gate reads. A tier upgrade is a single UPDATE to this row; adapter
code and environment variables never change (task-1764 DoD, proven by
tests).

This module is a pure rule with no I/O — storage (the `source_contract`
table) belongs to `ports/source_contract_repository.py` +
`adapters/postgres_source_contract.py`, and wiring this gate in front of
adapters that only know `source_id` belongs to
`application/authorize_source_access.py`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import AwareDatetime, BaseModel, model_validator

__all__ = [
    "SourceContractTier",
    "RedistributionScope",
    "DataUse",
    "SourceCapability",
    "SourceContract",
    "SourceContractDenialReason",
    "SourceContractGrant",
    "authorize_source",
    "permits_use",
]


class SourceContractTier(str, Enum):
    FREE = "FREE"
    PERSONAL = "PERSONAL"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"


class RedistributionScope(str, Enum):
    """D2/D7. Unspecified is treated as `NONE` — "the same fail-closed
    discipline as existing entitlements" (D2)."""

    NONE = "NONE"
    USER_SCOPED = "USER_SCOPED"
    INTERNAL = "INTERNAL"
    DISPLAY = "DISPLAY"
    REDISTRIBUTE = "REDISTRIBUTE"


class DataUse(str, Enum):
    """The enforcement points D2 pins down (read API/chart/backtest/export)
    plus D7's "display to the connection owner themself", split into
    determinable use purposes. Input to `permits_use()`; enforcement-point
    code must classify its own request as one of these values and delegate
    the determination (never reimplement string comparison)."""

    INTERNAL_CALC = "INTERNAL_CALC"
    USER_OWN_DISPLAY = "USER_OWN_DISPLAY"
    SHARED_DISPLAY = "SHARED_DISPLAY"
    EXPORT_OR_RESELL = "EXPORT_OR_RESELL"


_PERMITTED_USES: dict[RedistributionScope, frozenset[DataUse]] = {
    RedistributionScope.NONE: frozenset(),
    RedistributionScope.USER_SCOPED: frozenset({DataUse.USER_OWN_DISPLAY}),
    RedistributionScope.INTERNAL: frozenset({DataUse.INTERNAL_CALC}),
    RedistributionScope.DISPLAY: frozenset(
        {DataUse.INTERNAL_CALC, DataUse.USER_OWN_DISPLAY, DataUse.SHARED_DISPLAY}
    ),
    RedistributionScope.REDISTRIBUTE: frozenset(
        {
            DataUse.INTERNAL_CALC,
            DataUse.USER_OWN_DISPLAY,
            DataUse.SHARED_DISPLAY,
            DataUse.EXPORT_OR_RESELL,
        }
    ),
}


def permits_use(scope: RedistributionScope, use: DataUse) -> bool:
    """The pure-determination half of D2 "block by structure, not
    declaration". `USER_SCOPED` permits only `USER_OWN_DISPLAY` — it must
    never be used for a shared cache, screener, or another user's response
    assembly (`SHARED_DISPLAY`) (D7)."""
    return use in _PERMITTED_USES[scope]


class SourceCapability(BaseModel, frozen=True):
    """D6 "capability description (asset class/resolution/whether corporate
    actions are covered)". Asset class and resolution vocabularies differ
    per source (e.g. ECOS uses macro indicators, KRX uses asset classes),
    so these are kept as strings rather than narrowed to the trading
    `AssetClass`/`Timeframe` enums."""

    asset_classes: frozenset[str]
    resolutions: frozenset[str]
    corporate_actions: bool = False


class SourceContract(BaseModel, frozen=True):
    """A pure representation of one contract row, carried over directly from
    the D1 table. `credential_ref` is merely a keyring-handle string (D1
    "the real key is not here") — this model has no field that could hold
    the raw key, so it cannot leak even when serialized."""

    source_id: str
    tier: SourceContractTier
    credential_ref: str
    redistribution_scope: RedistributionScope
    rate_limit: int
    quota: int
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    capability: SourceCapability

    @model_validator(mode="after")
    def _valid_range(self) -> SourceContract:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to는 valid_from보다 뒤여야 한다")
        return self


class SourceContractDenialReason(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"


class SourceContractGrant(BaseModel, frozen=True):
    """Gate determination result. Enforces that only mutually exclusive
    allow/deny field combinations can be constructed (same pattern as
    `domain/entitlement/policy.py`'s `Entitlement`)."""

    allowed: bool
    tier: SourceContractTier | None
    redistribution_scope: RedistributionScope | None
    rate_limit: int | None
    quota: int | None
    capability: SourceCapability | None
    denial_reason: SourceContractDenialReason | None

    @model_validator(mode="after")
    def _allow_deny_are_exclusive(self) -> SourceContractGrant:
        grant_fields = (
            self.tier,
            self.redistribution_scope,
            self.rate_limit,
            self.quota,
            self.capability,
        )
        if self.allowed:
            if self.denial_reason is not None or any(f is None for f in grant_fields):
                raise ValueError(
                    "allowed=True는 계약 필드를 전부 채우고 denial_reason은 비워야 한다"
                )
        else:
            if self.denial_reason is None or any(f is not None for f in grant_fields):
                raise ValueError("allowed=False는 denial_reason만 채우고 계약 필드는 비워야 한다")
        return self


def _deny(reason: SourceContractDenialReason) -> SourceContractGrant:
    return SourceContractGrant(
        allowed=False,
        tier=None,
        redistribution_scope=None,
        rate_limit=None,
        quota=None,
        capability=None,
        denial_reason=reason,
    )


def authorize_source(contract: SourceContract | None, as_of: datetime) -> SourceContractGrant:
    """A row looked up by `source_id` (may be absent) -> whether access is
    allowed.

    Fail-closed: denied in every case where the row is missing
    (`NOT_FOUND`), not yet in effect (`NOT_YET_VALID`), or expired
    (`EXPIRED`) — "missing information means deny" (same principle as
    `domain/entitlement/policy.py`).

    `as_of` is the deterministic clock input the caller passes in (a pure
    function never reads the current time itself).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of는 tz-aware datetime만 받는다")

    if contract is None:
        return _deny(SourceContractDenialReason.NOT_FOUND)
    if as_of < contract.valid_from:
        return _deny(SourceContractDenialReason.NOT_YET_VALID)
    if contract.valid_to is not None and as_of >= contract.valid_to:
        return _deny(SourceContractDenialReason.EXPIRED)

    return SourceContractGrant(
        allowed=True,
        tier=contract.tier,
        redistribution_scope=contract.redistribution_scope,
        rate_limit=contract.rate_limit,
        quota=contract.quota,
        capability=contract.capability,
        denial_reason=None,
    )
