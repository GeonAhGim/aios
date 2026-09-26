"""MP-2 -- marketplace/domain/visibility.py: four-tier visibility rule (pure domain).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.7(MP)
MP-2 (depends on MP-1 `marketplace/contracts/v1.py`).

§3.5 `ListingVisibility = PUBLIC|PROTECTED|INVITE|PRIVATE` names the four
tiers; this leaf owns judging what each tier grants a given viewer --
listing discoverability and, separately, script *source* exposure. The two
are independent axes: a `PROTECTED` listing is discoverable and
subscribable, but its source is never exposed to anyone but the owner
(server-side execution only, publishing the reproduction key --
`ReproductionKey`, MP-1 -- in place of source access).

`has_protected_source` is a third, orthogonal fact (the listing bundles
material the seller is not licensed to publish, e.g. an unlicensed
indicator or strategy code) -- a listing carrying that fact can never be
requested at `PUBLIC` visibility, regardless of what the seller intends.
`validate_visibility_grade` enforces this at the input boundary so a bad
request never reaches `resolve_visibility`.

ADR-2026-09-26-B Decision 2 (SRC-1) adds a fourth, independent axis:
`source_retention` -- whether the server keeps only the signed IR
(`artifact`) or the original source. `validate_source_retention` is this
leaf's policy-judgment slice of SRC-1 only: which `(visibility,
retention)` pairs are legal. Encryption-key management and the at-rest
encrypted-column storage that `SOURCE_STORED` would actually require are
OUT OF SCOPE for this leaf and land in a follow-up tier-S leaf; this
function never touches key material, it only decides whether the pair is
allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.foundation.marketplace.contracts.v1 import ListingVisibility, MarketplaceErrorCode

SourceRetention = Literal["IR_ONLY", "SOURCE_STORED"]

_SOURCE_RETENTION_VALUES: frozenset[str] = frozenset({"IR_ONLY", "SOURCE_STORED"})

_SOURCE_STORED_RESTRICTED_GRADES: tuple[ListingVisibility, ...] = (
    ListingVisibility.PROTECTED,
    ListingVisibility.INVITE,
    ListingVisibility.PRIVATE,
)


@dataclass(frozen=True)
class ViewerContext:
    """Facts about the viewer a visibility decision is made for. Not
    mutually exclusive -- the owner is also technically invited in some
    flows, so callers pass whatever is true; `resolve_visibility` checks
    `is_owner` first regardless of the other flags."""

    is_owner: bool = False
    is_invited: bool = False


@dataclass(frozen=True)
class VisibilityDecision:
    """Result of resolving a `(visibility, has_protected_source, viewer)`
    triple. `listing_visible` gates whether the listing (metadata, price,
    reputation, ...) appears to the viewer at all; `source_visible` gates
    the script source specifically and is always a subset of
    `listing_visible` -- source can never be visible when the listing
    itself is not."""

    listing_visible: bool
    source_visible: bool


def validate_visibility_grade(
    visibility: ListingVisibility, *, has_protected_source: bool
) -> None:
    """Reject visibility grades that are structurally impossible given the
    listing's protected-source fact.

    `has_protected_source=True` strategies (license-unauthorized indicator
    or strategy code) can never be requested at `PUBLIC` -- that tier
    grants source access outright, so honoring the request would leak the
    protected material. Every other combination (including
    `PROTECTED`/`INVITE`/`PRIVATE` with `has_protected_source=True`) is
    structurally fine because none of those tiers exposes source to an
    arbitrary viewer.
    """
    if has_protected_source and visibility is ListingVisibility.PUBLIC:
        raise ValueError(
            f"{MarketplaceErrorCode.VISIBILITY_DENIED.value}: "
            "a listing with protected source material cannot be requested "
            "at PUBLIC visibility"
        )


def validate_source_retention(
    visibility: ListingVisibility, retention: SourceRetention = "IR_ONLY"
) -> None:
    """Reject `source_retention="SOURCE_STORED"` on any grade other than
    `PUBLIC`.

    `PROTECTED`/`INVITE`/`PRIVATE` gate script source to a subset of
    viewers (or nobody but the owner) precisely because the server never
    hands out anything but the signed IR (`artifact`) at request time --
    persisting the original source at rest at any of those grades would
    defeat that boundary the moment retention outlives one request, so it
    is explicitly denied rather than left to depend on encryption
    (encryption-key management is a separate, out-of-scope follow-up
    tier-S leaf). `PUBLIC` already grants source to everyone, so storing
    it changes no exposure and is permitted. Default is `"IR_ONLY"`,
    matching the spec's PROTECTED-and-above default.

    This check is independent of `validate_visibility_grade`: that one
    judges `(visibility, has_protected_source)`, this one judges
    `(visibility, retention)`. A listing can violate either, both, or
    neither -- callers should call both before persisting a listing.

    Raises `ValueError` for a disallowed `(visibility, retention)` pair,
    or for a `retention` value outside the known two (fail-closed on a
    corrupted value that reached this boundary from outside the enum).
    """
    if retention not in _SOURCE_RETENTION_VALUES:
        raise ValueError(
            f"{MarketplaceErrorCode.VISIBILITY_DENIED.value}: "
            f"unknown source_retention {retention!r}"
        )
    if retention == "SOURCE_STORED" and visibility in _SOURCE_STORED_RESTRICTED_GRADES:
        raise ValueError(
            f"{MarketplaceErrorCode.VISIBILITY_DENIED.value}: "
            f"{visibility!r} listings cannot set source_retention=SOURCE_STORED "
            "-- only IR_ONLY is permitted below PUBLIC"
        )


def resolve_visibility(
    visibility: ListingVisibility,
    *,
    has_protected_source: bool,
    viewer: ViewerContext,
) -> VisibilityDecision:
    """Judge listing- and source-visibility for one viewer under the §3.5
    four-tier rule. Pure function -- no I/O, no DB lookups; the caller
    resolves `viewer` (invite membership etc.) before calling in.

    Rules (owner always sees everything):
    - `PUBLIC`: listing and source visible to everyone.
    - `PROTECTED`: listing visible to everyone; source never exposed to
      anyone but the owner -- server-side execution only (§3.4
      `ReproductionKey` stands in for source access).
    - `INVITE`: listing and source visible to the owner and invited
      viewers only; everyone else sees neither.
    - `PRIVATE`: listing and source visible to the owner only.

    Raises `ValueError` (via `validate_visibility_grade`) if `visibility`
    is `PUBLIC` while `has_protected_source` is true -- that combination
    must never reach a per-viewer decision, even for the owner.
    """
    validate_visibility_grade(visibility, has_protected_source=has_protected_source)

    if viewer.is_owner:
        return VisibilityDecision(listing_visible=True, source_visible=True)

    if visibility is ListingVisibility.PUBLIC:
        return VisibilityDecision(listing_visible=True, source_visible=True)

    if visibility is ListingVisibility.PROTECTED:
        return VisibilityDecision(listing_visible=True, source_visible=False)

    if visibility is ListingVisibility.INVITE:
        visible = viewer.is_invited
        return VisibilityDecision(listing_visible=visible, source_visible=visible)

    if visibility is ListingVisibility.PRIVATE:
        return VisibilityDecision(listing_visible=False, source_visible=False)

    raise ValueError(
        f"{MarketplaceErrorCode.VISIBILITY_DENIED.value}: "
        f"unknown visibility grade {visibility!r}"
    )
