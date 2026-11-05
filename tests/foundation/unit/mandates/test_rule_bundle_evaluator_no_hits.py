"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — no-hit / empty-snapshot tests
for `evaluator.py`.

Split from `test_rule_bundle_evaluator.py` (task-22054): isolates the
`test_no_hits_allows` test so that it does not share a file with the
CPU-heavy `test_evaluate_bundle_meets_latency_budget_over_many_calls`
perf test — parallel CI (`pytest_perf_parallel_2`) on the same file
causes wall-clock contention that makes perf budgets flaky.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec

_NOW = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def _allow(_params: dict, _snapshot: dict) -> RuleHit | None:
    return None


def _bundle(*rules: RuleSpec, version: str = "v1") -> RuleBundle:
    return RuleBundle(version=version, rules=tuple(rules))


def test_no_hits_allows():
    """No rule hits → ALLOW (default verdict)."""
    allow_rule = RuleSpec(rule_id="R_ALLOW", params={}, check=_allow)
    decision = evaluate_bundle(_bundle(allow_rule), {}, now=_NOW)
    assert decision.verdict == ComplianceVerdict.ALLOW
    assert decision.rule_hits == []
