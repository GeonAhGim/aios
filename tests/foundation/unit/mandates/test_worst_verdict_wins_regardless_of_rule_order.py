"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — isolated negative test for
worst-verdict-wins order independence.

Split from test_rule_bundle_evaluator.py (task-22052) to prevent
pytest-xdist parallel interference: the original file ran 14 tests
concurrently under -n 2, and the perf test's heavy loop occasionally
contaminated shared module-level fixtures in other workers.

This test is the only one that exercises the worst-verdict-wins policy
across multiple rules in different orders — it deserves its own file
so it can be targeted by CI parallel scheduling without dragging in
the perf or replay tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec

_NOW = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def _allow(params, snapshot):
    return None


def _warn(params, snapshot):
    return RuleHit(rule_id="R_WARN", severity=ComplianceVerdict.WARN, message="warn", evidence={})


def _deny(params, snapshot):
    return RuleHit(rule_id="R_DENY", severity=ComplianceVerdict.DENY, message="deny", evidence={})


def _bundle(*rules):
    return RuleBundle(version="v1", rules=tuple(rules))


def test_worst_verdict_wins_regardless_of_rule_order():
    """Order independence negative test: DENY must win regardless of rule order.

    Forward (allow, warn, deny) and reversed (deny, warn, allow) bundles
    must produce the same verdict — CM-3 order-independence invariant.
    """
    allow_rule = RuleSpec(rule_id="R_ALLOW", params={}, check=_allow)
    warn_rule = RuleSpec(rule_id="R_WARN", params={}, check=_warn)
    deny_rule = RuleSpec(rule_id="R_DENY", params={}, check=_deny)

    forward = _bundle(allow_rule, warn_rule, deny_rule)
    reversed_bundle = _bundle(deny_rule, warn_rule, allow_rule)

    snapshot = {"asset": "BTC"}
    forward_decision = evaluate_bundle(forward, snapshot, now=_NOW)
    reversed_decision = evaluate_bundle(reversed_bundle, snapshot, now=_NOW)

    assert forward_decision == reversed_decision
    assert forward_decision.verdict == ComplianceVerdict.DENY
