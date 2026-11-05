"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — bundle_hash cross-check against canonical_json.

Split from `test_rule_bundle_evaluator.py` (task-22058) to reduce parallel
pytest-xdist contention on the original file. This test is independent: it
only imports `RuleSpec`, `RuleBundle`, `canonical_json`, `sha256_hex` — no
shared fixtures, no global state.
"""

from __future__ import annotations

from typing import Any

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec


def _allow(_params: Any, _snapshot: Any) -> None:
    return None


def _bundle(*rules: RuleSpec, version: str = "v1") -> RuleBundle:
    return RuleBundle(version=version, rules=tuple(rules))


def test_bundle_hash_matches_direct_canonical_json_reuse():
    """Cross-check against the same R-01 utility this module claims to
    reuse rather than reimplement.

    `RuleBundle.bundle_hash()` must equal `sha256_hex(canonical_json(payload))`
    for the same bundle configuration — proving the hash utility is reused
    from `src.core.risk.hashing` (CM-3의 hash 재사용 검증)."""
    rule = RuleSpec(rule_id="R_1", params={"max_pct": 10.0}, check=_allow)
    bundle = _bundle(rule, version="v2")
    payload = {"version": "v2", "rules": [{"rule_id": "R_1", "params": {"max_pct": 10.0}}]}
    expected = sha256_hex(canonical_json(payload))
    assert bundle.bundle_hash() == expected
