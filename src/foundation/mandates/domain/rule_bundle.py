"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — `RuleBundle`: a versioned,
order-independent set of compliance rule checks + its content hash.

`domain/rules/{restricted_list,concentration,leverage,liquidity,exclusion,
short_sale,wash_trade,position_limit}.py` (CM-6/CM-7/CM-9) do not exist yet
— this module only defines the shape a rule slots into (`RuleSpec.check`)
and how a set of them is identified (`bundle_hash`). Hashing reuses R-01's
`canonical_json`/`sha256_hex` (`src.core.risk.hashing`) the same way R-15's
`policy_bundle.compute_rule_hash` does — this module does not reimplement
normalization or sha256 (decision note: "reuse R-01/R-15's policy-bundle
hash util, do not reimplement").
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import RuleHit

RuleCheck = Callable[[Mapping[str, Any], Mapping[str, Any]], RuleHit | None]
"""`(params, snapshot) -> RuleHit | None`. `None` means the rule found
nothing to report. A raised exception is the evaluator's problem to turn
into fail-closed DENY (`domain/evaluator.py`), not this module's."""


@dataclass(frozen=True)
class RuleSpec:
    """One rule slot in a bundle. CM-3 does not ship real rule bodies —
    callers (tests here, real `domain/rules/*.py` from CM-6/CM-7/CM-9
    later) supply `check` directly."""

    rule_id: str
    params: Mapping[str, Any]
    check: RuleCheck


@dataclass(frozen=True)
class RuleBundle:
    """A named, versioned set of `RuleSpec`s.

    §9 CM-3 "order-independent" — the set is unordered by contract, so
    `bundle_hash()` sorts by `rule_id` before hashing regardless of the
    order `rules` was constructed in.
    """

    version: str
    rules: tuple[RuleSpec, ...]

    def __post_init__(self) -> None:
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("RuleBundle.rules must have unique rule_id values")

    def bundle_hash(self) -> str:
        """Content hash over `version` + each rule's `(rule_id, params)`.

        The `check` callable itself carries no serializable data and is
        excluded — this hash identifies *which configuration* is active,
        not *which code happens to be imported*, matching R-15's
        `compute_rule_hash` treatment of `RiskPolicy` vs. `engine_version`.
        """
        sorted_rules = sorted(self.rules, key=lambda rule: rule.rule_id)
        payload = {
            "version": self.version,
            "rules": [
                {"rule_id": rule.rule_id, "params": dict(rule.params)}
                for rule in sorted_rules
            ],
        }
        return sha256_hex(canonical_json(payload))
