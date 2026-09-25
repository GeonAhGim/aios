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
"""

from __future__ import annotations

from dataclasses import dataclass

from src.foundation.marketplace.contracts.v1 import ListingVisibility, MarketplaceErrorCode


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
