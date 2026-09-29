"""FA-24 — LegalEntity region tag storage-location policy (pure, no I/O).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-24
(table row: `entities/domain/region.py` + storage-location policy + adversarial
"other-region storage rejected"; §1 "data sovereignty·keys"; §2.6; §3
`FA_REGION_DENIED`(403)).

`LegalEntity.region_tag` (FA-1/FA-2) already carries the raw tenant/entity
region label; this module is the single place that turns that label into an
allow/deny decision for "which physical storage region may this entity's data
be written to". Every write adapter that persists per-entity rows in more
than one physical region must call `assert_storage_region_allowed` before the
write — this module does not perform the write itself (no I/O), it only
decides.

Fail-closed by construction: an unregistered `region_tag` denies (raises
`UnknownRegionTagError`) instead of silently defaulting to "allow everywhere"
or "allow nowhere" — a missing policy entry is a configuration bug that must
surface immediately, not a value to guess (same fail-closed posture as
`hierarchy.py`'s `_require_open_parent`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import ClassVar

from src.foundation.entities.contracts.v1 import EntityErrorCode, LegalEntity


class RegionDeniedError(ValueError):
    """FA_REGION_DENIED(403) — the entity's `region_tag` does not allow
    writing to `storage_region`."""

    code: ClassVar[EntityErrorCode] = EntityErrorCode.REGION_DENIED

    def __init__(self, entity: LegalEntity, storage_region: str) -> None:
        self.entity_id = entity.entity_id
        self.region_tag = entity.region_tag
        self.storage_region = storage_region
        super().__init__(
            f"LegalEntity {entity.entity_id}(region_tag={entity.region_tag!r}) "
            f"may not be stored in region {storage_region!r}"
        )


class UnknownRegionTagError(ValueError):
    """`region_tag` has no policy entry at all. Raised instead of treating an
    unknown tag as either "allow everywhere" (data-sovereignty breach) or
    "allow nowhere forever" (silently no-ops every write) — the caller must
    register the tag in the policy before entities can use it."""

    code: ClassVar[EntityErrorCode] = EntityErrorCode.REGION_DENIED

    def __init__(self, region_tag: str) -> None:
        self.region_tag = region_tag
        super().__init__(f"region_tag={region_tag!r} has no storage policy entry")


class RegionStoragePolicy:
    """Immutable map of `region_tag -> allowed storage regions`.

    Most tags allow exactly one storage region (their own) — a second entry
    is only added for an explicit DR/replica decision, never inferred."""

    __slots__ = ("_allowed",)

    def __init__(self, allowed: Mapping[str, Iterable[str]]) -> None:
        self._allowed: dict[str, frozenset[str]] = {
            tag: frozenset(regions) for tag, regions in allowed.items()
        }

    def allowed_regions(self, region_tag: str) -> frozenset[str]:
        try:
            return self._allowed[region_tag]
        except KeyError:
            raise UnknownRegionTagError(region_tag) from None

    def is_allowed(self, region_tag: str, storage_region: str) -> bool:
        return storage_region in self.allowed_regions(region_tag)


def same_region_policy(region_tags: Iterable[str]) -> RegionStoragePolicy:
    """Default policy generator: every tag may only be stored in the region
    it names — no cross-region replica is assumed."""
    return RegionStoragePolicy({tag: {tag} for tag in region_tags})


def assert_storage_region_allowed(
    entity: LegalEntity, storage_region: str, policy: RegionStoragePolicy
) -> None:
    """Single enforcement point (§9 FA-24 DoD "region tag enforced"). Storage
    adapters call this before persisting an entity-scoped row; it raises
    instead of returning a bool so a caller cannot forget to check the
    result and write anyway."""
    if not policy.is_allowed(entity.region_tag, storage_region):
        raise RegionDeniedError(entity, storage_region)
