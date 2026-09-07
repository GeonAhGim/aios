"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — bundle evaluator.

Deterministic, rule-order independent, worst-verdict-wins (DENY > WARN >
ALLOW), mirroring R-16 `core/risk/evaluator.py`'s outcome composition. Any
exception a rule check raises is caught here and turned into a DENY hit
instead of propagating — CM-A2/I-09 fail-closed: a broken rule must never
silently let an order through. Pure function, no I/O, no LLM call (I-09):
`now` is injected by the caller exactly like R-16's `evaluate(..., now=...)`,
so replaying the same `(bundle, snapshot, now)` always reproduces the same
`ComplianceDecision` (CM-A4 "explain() always reproduces the same result").
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import ComplianceDecision, ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec

# Fixed namespace for deriving decision_id — same technique as R-16
# `core/risk/evaluator._DECISION_ID_NAMESPACE`: no randomness, so the id is
# a pure function of (bundle_version, inputs_hash) instead of uuid4().
_DECISION_ID_NAMESPACE = UUID("6f6e6cf4-6b0a-4e6a-8f3e-9d6f6a4c9a01")

_VERDICT_RANK: dict[ComplianceVerdict, int] = {
    ComplianceVerdict.ALLOW: 0,
    ComplianceVerdict.WARN: 1,
    ComplianceVerdict.DENY: 2,
}


def _run_rule(rule: RuleSpec, snapshot: Mapping[str, Any]) -> RuleHit | None:
    try:
        return rule.check(rule.params, snapshot)
    except Exception:  # noqa: BLE001 — CM-A2 fail-closed: a broken rule denies, it never passes silently
        return RuleHit(
            rule_id=rule.rule_id,
            severity=ComplianceVerdict.DENY,
            message=f"{rule.rule_id} raised during evaluation",
            evidence={},
        )


def evaluate_bundle(
    bundle: RuleBundle,
    snapshot: Mapping[str, Any],
    *,
    now: datetime,
) -> ComplianceDecision:
    """§9 CM-3 public contract. `now` must be tz-aware UTC (caller-injected,
    same reason as R-16: this function never reads the clock itself)."""
    hits = [hit for hit in (_run_rule(rule, snapshot) for rule in bundle.rules) if hit is not None]
    hits.sort(key=lambda hit: hit.rule_id)  # rule-order independent output

    verdict = ComplianceVerdict.ALLOW
    for hit in hits:
        if _VERDICT_RANK[hit.severity] > _VERDICT_RANK[verdict]:
            verdict = hit.severity

    inputs_hash = sha256_hex(canonical_json(dict(snapshot)))
    bundle_version = bundle.bundle_hash()
    decision_id = uuid5(_DECISION_ID_NAMESPACE, f"{bundle_version}:{inputs_hash}")

    return ComplianceDecision(
        decision_id=decision_id,
        verdict=verdict,
        rule_hits=hits,
        inputs_hash=inputs_hash,
        bundle_version=bundle_version,
        evaluated_at=now,
    )
