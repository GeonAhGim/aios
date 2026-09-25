"""L4_compliance_and_regulatory_v1.0.md#9 CM-8 — read-only preview sibling
of `evaluate_pre_trade.py`, split into its own file to stay under the
architecture guard's 300-line cap (`P6.line_cap`, `common.SRC_LINE_CAP`)
rather than growing `evaluate_pre_trade.py` past it (task-2656 set the
precedent: split, don't raise the cap).

Added for UX-10 `whatif/application/preview_order.py`
(docs/specs/L4_product_experience_and_discovery_v1.0.md#UX-10, §4 UX-A2 —
the what-if path performs no writes at all). `evaluate_pre_trade()` always
either returns a cached decision or persists a new `policy_decision` row
(`_ensure_bundle_row`/`insert_policy_decision`) — never safe for a
read-only caller. `preview_pre_trade()` below never touches
`MandateRepository` at all: no mandate/revision lookup, no cache read, no
write. It also never imports `mandates.domain.*` — cross-aggregate callers
are only allowed through `application/`/`contracts/`
(`.importlinter` `boundary:foundation-aggregates`), so this takes the
`contracts.v1.MandateRevisionView` public contract type, not the internal
domain `MandateRevision` dataclass `assemble_rule_bundle` needs.

Narrower than `assemble_rule_bundle` by necessity: `MandateRevisionView`
doesn't carry the CM-2 fields (`esg_excluded_symbols`/`max_leverage_ratio`)
`assemble_rule_bundle` also wires — only `restricted_list` +
`concentration` are activated here, the same two rules
`assemble_rule_bundle`'s own docstring says are the only ones wired
end-to-end from real per-order snapshots today.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, MandateRevisionView
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules.concentration import check as _check_concentration
from src.foundation.mandates.domain.rules.restricted_list import check as _check_restricted_list


@dataclass(frozen=True)
class PreTradePreview:
    """Read-only sibling of `evaluate_pre_trade.PreTradeResult` for a
    *preview*, not a judgment of record — no `compliance_decision_id`
    because nothing is persisted."""

    verdict: ComplianceVerdict
    reason_codes: tuple[str, ...]


def _preview_rule_bundle(revision: MandateRevisionView, snapshot: Mapping[str, Any]) -> RuleBundle:
    specs: list[RuleSpec] = []
    if "symbol" in snapshot:
        specs.append(
            RuleSpec(
                rule_id="restricted_list",
                params={"restricted_symbols": tuple(revision.forbidden_assets)},
                check=_check_restricted_list,
            )
        )
    if "projected_instrument_pct" in snapshot:
        specs.append(
            RuleSpec(
                rule_id="concentration",
                params={"max_single_instrument_pct": revision.max_single_instrument_pct},
                check=_check_concentration,
            )
        )
    return RuleBundle(version="cm8-preview/v1", rules=tuple(specs))


def preview_pre_trade(
    revision: MandateRevisionView,
    snapshot: Mapping[str, Any],
    *,
    now: datetime,
) -> PreTradePreview:
    """Pure, zero-I/O preview of what `evaluate_pre_trade` would decide for
    this `(revision, snapshot)` pair. The caller is responsible for
    resolving the active `MandateRevisionView` first (a read it already
    needs to do outside this function) and for treating a missing/inactive
    mandate as fail-closed itself, the same way `evaluate_pre_trade` raises
    `ComplianceMandateMissingError`/`ComplianceBundleInactiveError` for
    those cases instead of silently defaulting.
    """
    bundle = _preview_rule_bundle(revision, snapshot)
    decision = evaluate_bundle(bundle, snapshot, now=now)
    return PreTradePreview(
        verdict=decision.verdict,
        reason_codes=tuple(hit.rule_id for hit in decision.rule_hits),
    )
