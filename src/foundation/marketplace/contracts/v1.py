"""MP-1 -- Marketplace listing contract v1.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.6,
§3.5, §4.3, §4.4.

This is the first leaf of the MP chain (MP-2..MP-9 all depend on it) and is
schema-only, on purpose: `ListingVisibility`/`SubscriptionTerms`/
`ReputationSnapshot`/`ReproductionKey` name the field shapes §3.5 and §3.4
describe, but none of the value logic those shapes imply -- the four-tier
visibility rule (`domain/visibility.py`), the §4.3 reputation formula
(`domain/reputation.py`), billing-period/trial/refund math
(`domain/subscription_rules.py`), and the sha256 composition of a
reproduction key -- exists yet. Those land in the later MP-2/4/6 leaves this
task's note points at. A JSON-schema snapshot test pins these shapes so a
later leaf cannot silently drop or retype a field while adding its logic.

`domain/*.py` modules import this file; this file does not import them
(standard 71 §4 contract-ownership layering, same as `ai/gateway/contracts/
v1.py` and `trust/contracts/v1.py`). Adding a field is a minor change
(standard 107, needs a default); removing or retyping one needs a new `v2`
module.
"""

from __future__ import annotations

import enum
import re
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = [
    "SCHEMA_VERSION",
    "ListingVisibility",
    "PriceKind",
    "MarketplaceErrorCode",
    "SubscriptionTerms",
    "ListingPrice",
    "CompatRange",
    "ReputationSnapshot",
    "ReproductionKey",
    "ScriptListing",
]

SCHEMA_VERSION: Literal["v1"] = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_hex(value: str) -> str:
    """Shape check only (64 lowercase hex chars) -- never recomputes a
    digest. The §3.4 `reproducibility_key = sha256(script_hash ||
    data_lineage_hash || rollup_version || config_hash || model_hash)`
    composition is BT-1's job, not this contract's."""
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError("must be a lowercase sha256 hex digest (64 hex chars)")
    return value


class ListingVisibility(str, enum.Enum):
    """spec §3.5 `Visibility = public|protected|invite|private` verbatim --
    inventing a fifth tier is forbidden, this enum is the single source of
    truth for MP-2's `domain/visibility.py`.

    `PROTECTED` means the script source is never exposed to a subscriber --
    server-side execution only, with the reproducibility key published
    instead of the source (MP-2 owns enforcing that; this contract only
    names the tier).
    """

    PUBLIC = "PUBLIC"
    PROTECTED = "PROTECTED"
    INVITE = "INVITE"
    PRIVATE = "PRIVATE"


class PriceKind(str, enum.Enum):
    """spec §3.5 `price: OneTime|Subscription{period, trial_days}`."""

    ONE_TIME = "ONE_TIME"
    SUBSCRIPTION = "SUBSCRIPTION"


class MarketplaceErrorCode(str, enum.Enum):
    """spec §3.5 error taxonomy verbatim -- the plagiarism/subscription/
    visibility leaves (MP-5/6/2) raise these, this contract only names
    them so callers can match on a closed set."""

    PLAGIARISM_SUSPECT = "MP_PLAGIARISM_SUSPECT"  # 409, review queue
    SUBSCRIPTION_STATE = "MP_SUBSCRIPTION_STATE"  # 409
    VISIBILITY_DENIED = "MP_VISIBILITY_DENIED"  # 403


class SubscriptionTerms(BaseModel, frozen=True):
    """spec §3.5 `price: Subscription{period, trial_days}`.

    `period_days` spells out the billing period as a concrete integer
    (rather than a free-text cadence like "monthly") because MP-6
    (`domain/subscription_rules.py`, period-key/pro-rated-refund math)
    needs a number to compute `(subscription_id, period_start)` idempotency
    keys (§5) -- deferring that shape choice to MP-6 would mean re-touching
    this contract then.
    """

    period_days: int = Field(gt=0)
    trial_days: int = Field(default=0, ge=0)
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ListingPrice(BaseModel, frozen=True):
    """spec §3.5 `price: OneTime|Subscription{...}` union, modeled as a
    tagged `kind` instead of a Python union type so `model_json_schema()`
    produces a stable, diffable shape for the snapshot test (a `Union`
    renders as `anyOf` and reorders across pydantic point releases --
    tests/foundation/unit/ai/gateway saw this exact class of instability,
    see requirements-lock.txt's pydantic pin note)."""

    kind: PriceKind
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    subscription: SubscriptionTerms | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _subscription_terms_match_kind(self) -> ListingPrice:
        if self.kind is PriceKind.SUBSCRIPTION and self.subscription is None:
            raise ValueError(
                f"{MarketplaceErrorCode.SUBSCRIPTION_STATE.value}: "
                "subscription pricing requires subscription terms"
            )
        if self.kind is PriceKind.ONE_TIME and self.subscription is not None:
            raise ValueError(
                f"{MarketplaceErrorCode.SUBSCRIPTION_STATE.value}: "
                "one-time pricing must not carry subscription terms"
            )
        return self


class CompatRange(BaseModel, frozen=True):
    """spec §3.5 `compat: {asset_classes, timeframes, min_history}`."""

    asset_classes: tuple[str, ...] = Field(min_length=1)
    timeframes: tuple[str, ...] = Field(min_length=1)
    min_history_days: int = Field(gt=0)
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ReputationSnapshot(BaseModel, frozen=True):
    """spec §3.5 `ReputationScore{verified_runs, reproduced_backtests,
    dispute_rate, weighted_score}`; §4.3 gives the weighting formula
    (`0.5*f(verified_runs) + 0.3*g(reproduced_backtests) +
    0.2*(1 - dispute_rate)`) that MP-4's `domain/reputation.py` computes --
    `weighted_score` here is a *reported* value this contract accepts as
    given, not a value it derives, which is what keeps MP-1 schema-only.
    """

    verified_runs: int = Field(ge=0)
    reproduced_backtests: int = Field(ge=0)
    dispute_rate: Decimal = Field(ge=0, le=1)
    weighted_score: Decimal = Field(ge=0, le=1)
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ReproductionKey(BaseModel, frozen=True):
    """spec §3.4 `reproducibility_key = sha256(script_hash ||
    data_lineage_hash || rollup_version || config_hash || model_hash)`.

    Every input component is carried alongside the final digest (`key`) --
    not just the opaque hex string -- so a listing can be re-verified
    against a later backtest run (MP-11's `verify_listing_backtest.py`)
    without needing to re-derive lineage out of a single hash. Composing
    `key` from the other fields is BT-1's job (`src/foundation/backtest`);
    this contract only validates that all five components and the digest
    are well-formed hex strings.
    """

    script_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    data_lineage_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    rollup_version: str = Field(min_length=1)
    config_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    model_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    key: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @field_validator("script_hash", "data_lineage_hash", "config_hash", "model_hash", "key")
    @classmethod
    def _check_hex_digest(cls, value: str) -> str:
        return _validate_sha256_hex(value)


class ScriptListing(BaseModel, frozen=True):
    """spec §3.5 `ScriptListing{listing_id, script_hash, version, changelog,
    visibility, price, compat, reputation}` verbatim, plus an optional
    `reproduction_key` (§3.4) -- `PROTECTED` listings (see
    `ListingVisibility`) publish the reproduction key in place of source
    access, so it has to be part of the listing a subscriber receives.

    §4.4 `draft -> compiled(hash fixed) -> published(visibility) ->
    deprecated; a published version's source/IR is immutable (an edit is a
    new version)` is why `frozen=True` here mirrors `AgentToken`/
    `ComplianceDecision` -- a listing object must not be mutable in memory
    once constructed; a new version is a new `ScriptListing`, not an
    in-place edit.
    """

    listing_id: UUID
    script_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    version: int = Field(gt=0)
    changelog: str = ""
    visibility: ListingVisibility
    price: ListingPrice
    compat: CompatRange
    reputation: ReputationSnapshot
    reproduction_key: ReproductionKey | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @field_validator("script_hash")
    @classmethod
    def _check_hex_digest(cls, value: str) -> str:
        return _validate_sha256_hex(value)
