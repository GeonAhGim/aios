"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — unit tests for
`rule_bundle.py` + `evaluator.py`.

Maps directly onto task-2037's 4 DoD items: (1) order-independent
worst-verdict-wins, (2) bundle_hash's sensitivity to a single-bit param
change, (3) rule exceptions fail-closed to DENY, (4) hash utility reuse
(cross-checked against `src.core.risk.hashing` directly instead of
reimplementing sha256/canonical_json).

DEEPEN 2037 (task-2856, docs/audit/DEPTH_CM.md): the original CM-3 leaf
graded D1 for missing numeric performance assertion, gate/CI red-regression
test, and D3 multi-instance/replay proof (the existing decision_id
determinism test is single-process only). Added below:
  - numeric performance: a wall-clock ceiling on 10,000 evaluations of a
    multi-rule bundle (pure-function regression guard, same pattern as
    task-2855's exclusion.py budget).
  - gate/CI red regression: `evaluator._VERDICT_RANK` must cover every
    `ComplianceVerdict` member — a future verdict added to the enum without
    a matching rank entry fails this test red immediately, instead of
    surfacing later as a silent `KeyError` deep in `evaluate_bundle`.
  - D3 multi-instance/replay proof: independent OS processes (not just
    repeated calls in one process) evaluating the same
    `(bundle, snapshot, now)` must produce byte-identical decisions,
    including `decision_id` — CM-A4 "explain() always reproduces the same
    result" holds across process boundaries, not only within one.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.evaluator import _VERDICT_RANK, evaluate_bundle
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
    assert any(
        hit.rule_id == "R_BROKEN" and hit.severity == ComplianceVerdict.DENY
        for hit in decision.rule_hits
    )


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


# --- Numeric performance (DEEPEN) -------------------------------------------


def test_evaluate_bundle_meets_latency_budget_over_many_calls() -> None:
    """수치 성능 단언: 규칙 3개짜리 번들을 10,000회 반복 평가한 총 지연이
    넉넉한 상한(1.0s, 호출당 평균 100us) 안에 들어야 한다 — 이후 회귀로
    순수 함수(정렬·해싱 포함)가 눈에 띄게 느려지면 이 테스트가 잡는다."""
    bundle = _bundle(
        RuleSpec(rule_id="R_ALLOW", params={"max_pct": 10.0}, check=_allow),
        RuleSpec(rule_id="R_WARN", params={}, check=_warn),
        RuleSpec(rule_id="R_DENY", params={}, check=_deny),
    )
    snapshot = {"asset": "BTC", "qty": 1.5}

    started = time.perf_counter()
    for _ in range(10_000):
        evaluate_bundle(bundle, snapshot, now=_NOW)
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 1.0, f"10,000 evaluations took {elapsed_s:.3f}s (budget 1.0s)"


# --- Gate/CI red regression guard (DEEPEN) -----------------------------------


def test_verdict_rank_covers_every_compliance_verdict_member() -> None:
    """게이트/CI 적색 회귀 가드: `evaluator._VERDICT_RANK`는 `ComplianceVerdict`의
    모든 멤버를 키로 가져야 한다. 향후 리프가 새 verdict(예: `INFO`)를 enum에
    추가하면서 이 순위표를 갱신하지 않으면, 그 verdict를 내는 규칙이 실제로
    평가될 때까지 숨어 있다가 `evaluate_bundle` 내부에서 `KeyError`로 터지는
    대신 이 테스트가 즉시 적색이 된다(CM-1의 `_OUTCOME_TO_VERDICT` 총량 가드와
    동일 패턴)."""
    assert set(_VERDICT_RANK.keys()) == set(ComplianceVerdict)


# --- D3 multi-instance/replay proof (DEEPEN) ---------------------------------


def _replay_in_subprocess(
    bundle: RuleBundle, snapshot: dict[str, Any], now: datetime
) -> tuple[str, str, str, str]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    decision = evaluate_bundle(bundle, snapshot, now=now)
    return (
        str(decision.decision_id),
        decision.verdict.value,
        decision.inputs_hash,
        decision.bundle_version,
    )


def test_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 (bundle, snapshot, now)를 각자 평가해도 완전히
    동일한 decision_id/verdict/inputs_hash/bundle_version을 내야 한다 —
    기존 결정성 테스트는 단일 프로세스 안 반복 호출에 그쳤으나, 이 테스트는
    프로세스 지역 상태(예: 메모리 주소 기반 해시, 임포트 순서)에 우연히
    기대는 비결정성이 없음을 실증한다(CM-A4)."""
    bundle = _bundle(
        RuleSpec(rule_id="R_WARN", params={"threshold": 5}, check=_warn),
        RuleSpec(rule_id="R_DENY", params={}, check=_deny),
    )
    snapshot = {"asset": "BTC", "qty": 2.0}

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [bundle] * 3, [snapshot] * 3, [_NOW] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
