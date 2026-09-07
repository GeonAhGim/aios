"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — `rule_bundle.py` +
`evaluator.py` 단위 테스트.

task-2037 DoD 4개 항목을 그대로 매핑한다: (1) 순서 무관 최악 판정,
(2) bundle_hash의 1비트 변화 민감도, (3) 규칙 예외 fail-closed DENY,
(4) 해시 유틸 재사용(직접 sha256/canonical_json을 다시 구현하지 않고
`src.core.risk.hashing`을 그대로 참조해 교차검증).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec

_NOW = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def _allow(_params: Any, _snapshot: Any) -> RuleHit | None:
    return None


def _warn(_params: Any, _snapshot: Any) -> RuleHit | None:
    return RuleHit(rule_id="R_WARN", severity=ComplianceVerdict.WARN, message="warn", evidence={})


def _deny(_params: Any, _snapshot: Any) -> RuleHit | None:
    return RuleHit(rule_id="R_DENY", severity=ComplianceVerdict.DENY, message="deny", evidence={})


def _raises(_params: Any, _snapshot: Any) -> RuleHit | None:
    raise RuntimeError("boom — simulated bug in a rule implementation")


def _bundle(*rules: RuleSpec, version: str = "v1") -> RuleBundle:
    return RuleBundle(version=version, rules=tuple(rules))


def test_worst_verdict_wins_regardless_of_rule_order():
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


def test_warn_only_bundle_does_not_escalate_to_deny():
    warn_rule = RuleSpec(rule_id="R_WARN", params={}, check=_warn)
    decision = evaluate_bundle(_bundle(warn_rule), {}, now=_NOW)
    assert decision.verdict == ComplianceVerdict.WARN


def test_no_hits_allows():
    allow_rule = RuleSpec(rule_id="R_ALLOW", params={}, check=_allow)
    decision = evaluate_bundle(_bundle(allow_rule), {}, now=_NOW)
    assert decision.verdict == ComplianceVerdict.ALLOW
    assert decision.rule_hits == []


def test_rule_exception_is_fail_closed_deny_not_swallowed_uncaught():
    ok_rule = RuleSpec(rule_id="R_ALLOW", params={}, check=_allow)
    broken_rule = RuleSpec(rule_id="R_BROKEN", params={}, check=_raises)

    # The exception itself must never escape evaluate_bundle — a caller
    # that forgot to catch it would otherwise let an order through with no
    # decision at all, which is worse than a wrong DENY.
    decision = evaluate_bundle(_bundle(ok_rule, broken_rule), {}, now=_NOW)

    assert decision.verdict == ComplianceVerdict.DENY
    assert any(hit.rule_id == "R_BROKEN" and hit.severity == ComplianceVerdict.DENY
               for hit in decision.rule_hits)


def test_bundle_hash_changes_when_one_param_bit_flips():
    baseline = _bundle(RuleSpec(rule_id="R_1", params={"max_pct": 10.0}, check=_allow))
    changed = _bundle(RuleSpec(rule_id="R_1", params={"max_pct": 10.5}, check=_allow))
    unchanged = _bundle(RuleSpec(rule_id="R_1", params={"max_pct": 10.0}, check=_deny))

    assert baseline.bundle_hash() != changed.bundle_hash()
    # check callable identity must not leak into the hash — only rule_id +
    # params identify the configuration (docstring contract in rule_bundle.py).
    assert baseline.bundle_hash() == unchanged.bundle_hash()


def test_bundle_hash_is_order_independent():
    a = RuleSpec(rule_id="R_A", params={"x": 1}, check=_allow)
    b = RuleSpec(rule_id="R_B", params={"y": 2}, check=_allow)
    assert _bundle(a, b).bundle_hash() == _bundle(b, a).bundle_hash()


def test_bundle_hash_matches_direct_canonical_json_reuse():
    # Cross-check against the same R-01 utility this module claims to
    # reuse rather than reimplement.
    rule = RuleSpec(rule_id="R_1", params={"max_pct": 10.0}, check=_allow)
    bundle = _bundle(rule, version="v2")
    payload = {"version": "v2", "rules": [{"rule_id": "R_1", "params": {"max_pct": 10.0}}]}
    expected = sha256_hex(canonical_json(payload))
    assert bundle.bundle_hash() == expected


def test_duplicate_rule_id_rejected():
    dup_a = RuleSpec(rule_id="R_DUP", params={}, check=_allow)
    dup_b = RuleSpec(rule_id="R_DUP", params={}, check=_deny)
    with pytest.raises(ValueError, match="unique"):
        _bundle(dup_a, dup_b)


def test_inputs_hash_is_sha256_hex_shape():
    rule = RuleSpec(rule_id="R_1", params={}, check=_allow)
    decision = evaluate_bundle(_bundle(rule), {"a": 1}, now=_NOW)
    assert len(decision.inputs_hash) == 64
    assert all(c in "0123456789abcdef" for c in decision.inputs_hash)


def test_compliance_decision_rejects_naive_datetime_via_evaluate_bundle():
    rule = RuleSpec(rule_id="R_1", params={}, check=_allow)
    with pytest.raises(ValidationError):
        evaluate_bundle(_bundle(rule), {}, now=datetime(2026, 9, 7))


def test_decision_id_is_deterministic_for_same_bundle_and_snapshot():
    rule = RuleSpec(rule_id="R_1", params={}, check=_allow)
    bundle = _bundle(rule)
    snapshot = {"asset": "ETH"}
    first = evaluate_bundle(bundle, snapshot, now=_NOW)
    second = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert first.decision_id == second.decision_id != uuid4()
