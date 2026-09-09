"""RD-3 -- domain/redistribution.py: license-gated body storage rule (pure).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1
domain/redistribution.py, §3 (`redistribution=link_only` source -> `body_ref`
always `None`, storing it is `RD_REDISTRIBUTION_DENIED`/403), §9 RD-3 (a)(b).

Two independent gates, both fail-closed, neither reimplemented here:

1. License gate -- RD-2's `SourceMeta.redistribution` (§3): a `link_only`
   source may never have `body_ref` set. Storing the body -- or a summary
   derived from it -- is exactly what the license forbids. This module only
   reads the flag `SourceMeta` already carries; it keeps no per-source table
   of its own.
2. Tier gate -- DC-27 `source_contract`
   (`src/foundation/market_data/domain/entitlement/source_contract.py`,
   task-1764/5406db6e). Whether this source's contract even permits use at
   all is `authorize_source`/`permits_use`'s determination
   (docs/design/ADR-2026-09-06-H D1/D2), looked up here via the caller-
   supplied `SourceContractGrant` -- RD-3 DoD (b) forbids redefining that
   tier table locally, so this module calls `permits_use` rather than
   re-deriving it from `grant.tier`.

Both gates must pass for a body to be storable; either one failing raises
the same `RedistributionViolationError` (RD_REDISTRIBUTION_DENIED, 403) --
the caller does not get to distinguish "wrong license" from "wrong tier"
and retry, both are not-retryable denials.
"""
from __future__ import annotations

from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    SourceContractGrant,
    permits_use,
)
from src.foundation.research_data.contracts.v1 import (
    ResearchDataErrorCode,
    ResearchItem,
    SourceMeta,
)

__all__ = ["RedistributionViolationError", "assert_redistribution_allowed"]


class RedistributionViolationError(ValueError):
    """`RD_REDISTRIBUTION_DENIED` (403) -- not retryable. Either the source's
    contract tier does not permit even internal use, or the source's license
    is `link_only` and the item carries a body anyway. The HTTP mapping is
    the API layer's job -- this module only performs the check."""

    error_code = ResearchDataErrorCode.REDISTRIBUTION_DENIED

    def __init__(self, item: ResearchItem, *, reason: str) -> None:
        self.item = item
        self.reason = reason
        super().__init__(
            f"{self.error_code.value}: item_id={item.item_id} "
            f"source_id={item.source_id} reason={reason}"
        )


def assert_redistribution_allowed(
    item: ResearchItem, source: SourceMeta, grant: SourceContractGrant
) -> None:
    """Passes if `item` may keep its `body_ref`, otherwise raises
    `RedistributionViolationError`.

    `source` must describe `item.source_id` and `grant` must be the
    DC-27 determination for that same source -- this function does not
    look either up itself (domain/** is I/O-free, L0-2), the caller
    (application layer) resolves both first.
    """
    if source.source_id != item.source_id:
        raise ValueError(
            "source.source_id does not match item.source_id -- "
            "caller must resolve the SourceMeta for this exact item"
        )

    if (
        not grant.allowed
        or grant.redistribution_scope is None
        or not permits_use(grant.redistribution_scope, DataUse.INTERNAL_CALC)
    ):
        raise RedistributionViolationError(item, reason="source_contract_denied")

    if source.redistribution == "link_only" and item.body_ref is not None:
        raise RedistributionViolationError(item, reason="link_only_body_present")
